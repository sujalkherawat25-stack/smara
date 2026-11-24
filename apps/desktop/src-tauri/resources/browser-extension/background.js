// Smara in the owner's own browser. Smara Desktop asks for a tab; this opens it (in a "Smara" group so the owner can watch),
// attaches the debugger to that tab only, and carries Smara's commands to it. Tabs Smara did not open are never read or
// controlled: every command is checked against the tabs this extension opened for that Smara task.
const HOST = "com.smara.desktop";
let host = null;
let paused = false;
const relays = new Map();      // relay id -> { socket, label, tabs: Set<tabId> }
const owner = new Map();       // tabId -> relay id
const groups = new Map();      // windowId -> Smara tab group id

function status() {
  const tabs = [...owner.keys()];
  chrome.storage.session.set({ status: { host: !!host, paused, tasks: [...relays.values()].map(r => r.label), tabs } }).catch(() => {});
  chrome.action.setBadgeText({ text: tabs.length ? String(tabs.length) : "" }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color: "#6d28d9" }).catch(() => {});
}

function connectHost() {
  if (host) return;
  try {
    host = chrome.runtime.connectNative(HOST);
  } catch (error) {
    host = null; status(); return;
  }
  host.onMessage.addListener(message => { if (message && message.type === "relays" && !paused) sync(message.relays || []); });
  host.onDisconnect.addListener(() => { host = null; status(); });
  host.postMessage({ type: "hello", browser: navigator.userAgent, version: chrome.runtime.getManifest().version });
  status();
}

function sync(list) {
  const wanted = new Set(list.map(r => r.id));
  for (const id of [...relays.keys()]) if (!wanted.has(id)) release(id);
  for (const relay of list) if (!relays.has(relay.id)) open(relay);
}

function open(relay) {
  const socket = new WebSocket(`ws://127.0.0.1:${relay.port}/extension/${relay.token}`);
  const entry = { socket, label: relay.label || "Smara", tabs: new Set() };
  relays.set(relay.id, entry);
  socket.onmessage = event => handle(relay.id, entry, JSON.parse(event.data)).catch(() => {});
  socket.onclose = () => release(relay.id);
  socket.onerror = () => {};
  status();
}

async function release(id) {
  const entry = relays.get(id);
  if (!entry) return;
  relays.delete(id);
  for (const tab of entry.tabs) {
    owner.delete(tab);
    await chrome.debugger.detach({ tabId: tab }).catch(() => {});
  }
  try { entry.socket.close(); } catch (error) {}
  status();
}

function send(entry, message) {
  if (entry.socket.readyState === WebSocket.OPEN) entry.socket.send(JSON.stringify(message));
}

async function targetInfo(tabId) {
  const info = await chrome.debugger.sendCommand({ tabId }, "Target.getTargetInfo");
  return info.targetInfo;
}

async function adopt(id, entry, tabId) {
  await chrome.debugger.attach({ tabId }, "1.3");
  entry.tabs.add(tabId);
  owner.set(tabId, id);
  status();
  return targetInfo(tabId);
}

async function group(tab, label) {
  try {
    let groupId = groups.get(tab.windowId);
    if (groupId !== undefined) {
      await chrome.tabs.group({ groupId, tabIds: [tab.id] });
    } else {
      groupId = await chrome.tabs.group({ tabIds: [tab.id], createProperties: { windowId: tab.windowId } });
      groups.set(tab.windowId, groupId);
      await chrome.tabGroups.update(groupId, { title: "Smara", color: "purple" });
    }
  } catch (error) {
    groups.delete(tab.windowId);          // the group was closed; the next tab starts a new one
  }
}

async function handle(id, entry, message) {
  const reply = result => send(entry, { type: "reply", id: message.id, result });
  const fail = error => send(entry, { type: "reply", id: message.id, error: String((error && error.message) || error) });
  try {
    if (message.type === "open") {
      const tab = await chrome.tabs.create({ url: "about:blank", active: true });
      await group(tab, message.label);
      const info = await adopt(id, entry, tab.id);
      reply({ tabId: tab.id, targetInfo: info });
    } else if (message.type === "cdp") {
      if (owner.get(message.tabId) !== id) throw new Error("Smara may only use the tabs it opened");
      const target = message.sessionId ? { tabId: message.tabId, sessionId: message.sessionId } : { tabId: message.tabId };
      reply(await chrome.debugger.sendCommand(target, message.method, message.params || {}));
    } else if (message.type === "close") {
      if (owner.get(message.tabId) !== id) throw new Error("Smara may only close the tabs it opened");
      await chrome.tabs.remove(message.tabId);
      reply({});
    } else if (message.type === "release") {
      for (const tab of entry.tabs) { owner.delete(tab); await chrome.debugger.detach({ tabId: tab }).catch(() => {}); }
      entry.tabs.clear();
      status();
      if (message.id !== undefined) reply({});
    }
  } catch (error) {
    fail(error);
  }
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  const id = owner.get(source.tabId);
  const entry = id && relays.get(id);
  if (entry) send(entry, { type: "event", tabId: source.tabId, sessionId: source.sessionId, method, params });
});

chrome.debugger.onDetach.addListener((source, reason) => {
  const id = owner.get(source.tabId);
  const entry = id && relays.get(id);
  owner.delete(source.tabId);
  if (entry) { entry.tabs.delete(source.tabId); send(entry, { type: "detached", tabId: source.tabId, reason }); }
  status();
});

// A page in one of Smara's tabs opened another tab (a popup or a link with target=_blank): it belongs to the same task.
chrome.tabs.onCreated.addListener(async tab => {
  const id = tab.openerTabId !== undefined ? owner.get(tab.openerTabId) : undefined;
  const entry = id && relays.get(id);
  if (!entry) return;
  try {
    await group(tab, entry.label);
    send(entry, { type: "tab", tabId: tab.id, targetInfo: await adopt(id, entry, tab.id) });
  } catch (error) {}
});

chrome.tabs.onRemoved.addListener(tabId => {
  const id = owner.get(tabId);
  const entry = id && relays.get(id);
  owner.delete(tabId);
  if (entry) { entry.tabs.delete(tabId); send(entry, { type: "detached", tabId }); }
  status();
});

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message && message.type === "pause") {
    paused = !!message.paused;
    if (paused) for (const id of [...relays.keys()]) release(id);
    else connectHost();
    status();
    respond({ paused });
  }
});

chrome.runtime.onStartup.addListener(connectHost);
chrome.runtime.onInstalled.addListener(connectHost);
chrome.alarms.create("smara-reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => { if (!host && !paused) connectHost(); });
connectHost();
