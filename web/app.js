const state = {
  tasks: [], research: [], schedules: [], actions: [], integrations: [], devices: [], cliDevices: [], conversations: [],
  selected: null, mode: 'development', bridgeToken: null, eventStreamAbort: null, eventIds: {},
  chatAbort: null, chatBusy: false,
  conversationId: localStorage.getItem('smara-conversation-id') || `chat_web_${crypto.randomUUID().replaceAll('-', '')}`,
  cliDeviceCode: new URLSearchParams(location.search).get('cli_device'),
};
const INTEGRATION_PROVIDERS = [
  { id: 'gmail', name: 'Gmail', detail: 'Search mail and approval-gated sending.', auth: 'oauth' },
  { id: 'calendar', name: 'Google Calendar', detail: 'Read events and approval-gated event creation.', auth: 'oauth' },
  { id: 'drive', name: 'Google Drive', detail: 'Search file metadata without downloading private files.', auth: 'oauth' },
  { id: 'github', name: 'GitHub', detail: 'List repositories and approval-gated content commits.', auth: 'oauth' },
  { id: 'telegram', name: 'Telegram', detail: 'Deliver approved updates and scheduled-task notifications.', auth: 'token' },
];
const $ = (selector) => document.querySelector(selector);
const account = $('#account');

// When embedded in the authenticated Smara shell, request the short-lived
// bridge token instead of relying on the parent guessing when this document's
// module has finished installing its message listener.  The parent validates
// the origin and iframe source before replying.
const CONTROL_PARENT_ORIGIN = 'https://ai.syntarus.com';
function announceControlReady() {
  if (window.parent === window) return;
  window.parent.postMessage({ type: 'smara-control-ready' }, CONTROL_PARENT_ORIGIN);
}
announceControlReady();
window.addEventListener('pageshow', announceControlReady);
account.value = localStorage.getItem('smara-account') || account.value;
account.addEventListener('change', () => { localStorage.setItem('smara-account', account.value.trim()); refresh(); });

function headers() {
  const result = {};
  if (state.bridgeToken) {
    result.Authorization = `Bearer ${state.bridgeToken}`;
    return result;
  }
  if (state.mode === 'development') result['X-Smara-Account-Id'] = account.value.trim() || 'local-user';
  return result;
}
async function api(path, options = {}) {
  const type = options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
  const response = await fetch(path, { ...options, headers: { ...type, ...headers(), ...(options.headers || {}) } });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Request failed (${response.status})`);
  return response.status === 204 ? null : response.json();
}
function notice(message, error = false) { const el = $('#notice'); el.textContent = message; el.style.color = error ? '#ffa4ae' : '#98edbf'; }
function badge(status) { return `<span class="badge ${status}">${status.replaceAll('_', ' ')}</span>`; }
function escape(text = '') { const node = document.createElement('span'); node.textContent = text; return node.innerHTML; }

async function refresh() {
  try {
    const health = await fetch('/health').then(r => r.json());
    state.mode = health.auth_mode;
    if (state.mode !== 'development' && !state.bridgeToken) {
      $('#auth-mode').textContent = 'Waiting for Smara sign-in…';
      return;
    }
    account.disabled = Boolean(state.bridgeToken);
    $('#auth-mode').textContent = state.bridgeToken ? 'Connected to your Smara account' : state.mode === 'development' ? 'Development account' : 'Signed-in gateway';
    [state.tasks, state.research, state.schedules, state.actions, state.integrations, state.devices, state.cliDevices, state.conversations] = await Promise.all([
      api('/v1/tasks'), api('/v1/research'), api('/v1/schedules'), api('/v1/integration-actions').then(x => x.actions), api('/v1/integrations').then(x => x.integrations), api('/v1/executors').then(x => x.executors), api('/v1/cli/devices').then(x => x.devices), api('/v1/conversations').then(x => x.conversations),
    ]);
    render();
    if (!state.chatBusy) await loadConversation(state.conversationId, { quiet: true });
    notice(`Updated ${new Date().toLocaleTimeString()}`);
  } catch (error) { notice(error.message, true); }
}

// The parent Smara app proves its httpOnly session to ai.syntarus.com, then
// sends a 60-second Control-only token here. The token is kept in memory only;
// no signing key, session cookie, or account identifier is exposed to this app.
window.addEventListener('message', (event) => {
  if (event.origin !== 'https://ai.syntarus.com') return;
  if (event.source !== window.parent) return;
  const data = event.data;
  if (!data || data.type !== 'smara-control-token' || typeof data.token !== 'string') return;
  state.bridgeToken = data.token;
  window.parent.postMessage({ type: 'smara-control-token-ack' }, CONTROL_PARENT_ORIGIN);
  maybeShowCliApproval();
  refresh();
});
function maybeShowCliApproval() {
  if (!state.cliDeviceCode || !state.bridgeToken) return;
  $('#cli-code').textContent = `CLI device …${state.cliDeviceCode.slice(-8)}`;
  $('#cli-expires').textContent = 'Approve this device to finish signing in. The request expires shortly.';
  $('#cli-approve').hidden = false;
  $('#cli-dialog').showModal();
}

function renderConversations() {
  const list = $('#conversation-list');
  const conversations = [...state.conversations];
  if (!conversations.some(item => item.id === state.conversationId)) {
    conversations.unshift({ id: state.conversationId, updated_at: null, next_sequence: 0 });
  }
  list.innerHTML = conversations.slice(0, 30).map(item => {
    const label = item.next_sequence ? `Conversation · ${Math.floor(item.next_sequence / 2)} turn(s)` : 'New conversation';
    const updated = item.updated_at ? new Date(item.updated_at).toLocaleString() : 'Not started';
    return `<button class="conversation ${item.id === state.conversationId ? 'active' : ''}" data-conversation="${escape(item.id)}"><b>${escape(label)}</b><small>${escape(updated)}</small></button>`;
  }).join('');
  document.querySelectorAll('[data-conversation]').forEach(button => button.onclick = () => selectConversation(button.dataset.conversation));
}

function setConversation(id) {
  state.conversationId = id;
  localStorage.setItem('smara-conversation-id', id);
  $('#chat-messages').dataset.loaded = '';
  renderConversations();
}

async function selectConversation(id) {
  if (state.chatAbort) state.chatAbort.abort();
  setConversation(id);
  await loadConversation(id);
}

function chatMessage(role, text = '') {
  const article = document.createElement('article');
  article.className = `message ${role}`;
  const label = document.createElement('b');
  label.textContent = role === 'user' ? 'You' : 'Smara';
  const content = document.createElement('div');
  content.className = 'message-content';
  content.textContent = text;
  article.append(label, content);
  $('#chat-messages').append(article);
  $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight;
  return content;
}

async function loadConversation(id, { quiet = false } = {}) {
  const messages = $('#chat-messages');
  if (quiet && messages.dataset.loaded === id) return;
  try {
    const turns = await api(`/v1/conversations/${encodeURIComponent(id)}/turns`).then(result => result.turns).catch(error => {
      if (/not found/i.test(error.message)) return [];
      throw error;
    });
    if (id !== state.conversationId || state.chatBusy) return;
    messages.replaceChildren();
    if (!turns.length) {
      messages.innerHTML = '<div class="empty"><h2>What can I help with?</h2><p>Short read-only work happens here. Long or risky work becomes a visible task with approvals.</p></div>';
    } else {
      turns.forEach(turn => chatMessage(turn.role, turn.content));
    }
    messages.dataset.loaded = id;
  } catch (error) {
    if (!quiet) notice(error.message, true);
  }
}

async function sendChat(message) {
  if (state.chatBusy) return;
  state.chatBusy = true;
  $('#chat-form button').disabled = true;
  const messages = $('#chat-messages');
  if (messages.querySelector('.empty')) messages.replaceChildren();
  chatMessage('user', message);
  const answer = chatMessage('assistant', '');
  const controller = new AbortController(); state.chatAbort = controller;
  $('#chat-progress').textContent = 'Starting…';
  try {
    const response = await fetch('/v1/chat/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...headers() }, signal: controller.signal,
      body: JSON.stringify({ message, conversation_id: state.conversationId, workspace_id: 'default' }),
    });
    if (!response.ok || !response.body) throw new Error((await response.json().catch(() => ({}))).detail || `Chat failed (${response.status})`);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split('\n\n'); buffer = frames.pop() || '';
      for (const frame of frames) {
        const line = frame.split('\n').find(item => item.startsWith('data: ')); if (!line) continue;
        const payload = JSON.parse(line.slice(6));
        if (payload.type === 'token') { answer.textContent += payload.text || ''; messages.scrollTop = messages.scrollHeight; }
        else if (payload.type === 'phase') $('#chat-progress').textContent = payload.phase === 'answer' ? 'Answering…' : `${payload.phase.replaceAll('_', ' ')}…`;
        else if (payload.type === 'status') $('#chat-progress').textContent = payload.label || 'Working…';
        else if (payload.type === 'tool_call') $('#chat-progress').textContent = `Using ${payload.name}…`;
        else if (payload.type === 'error') throw new Error(payload.message || 'Chat failed.');
        else if (payload.type === 'done') $('#chat-progress').textContent = `Done in ${(payload.total_ms / 1000).toFixed(1)}s`;
      }
    }
    if (!answer.textContent.trim()) answer.textContent = 'Smara completed the turn without a visible answer.';
    messages.dataset.loaded = state.conversationId;
    await refresh();
  } catch (error) {
    if (error.name !== 'AbortError') { answer.textContent = `I could not complete that turn: ${error.message}`; notice(error.message, true); }
  } finally {
    state.chatBusy = false;
    $('#chat-form button').disabled = false;
    if (state.chatAbort === controller) state.chatAbort = null;
  }
}

function render() {
  $('#task-total').textContent = state.tasks.length;
  const active = state.tasks.filter(t => !['completed', 'failed', 'cancelled'].includes(t.status));
  $('#task-list').innerHTML = state.tasks.length ? state.tasks.map(task => `<article class="task ${task.id === state.selected ? 'selected' : ''}" data-task="${task.id}"><div><h3>${escape(task.title)}</h3><p>${escape(task.objective.slice(0, 140))}</p></div>${badge(task.status)}</article>`).join('') : `<div class="empty">No tasks yet. Create work you want Smara to coordinate.</div>`;
  document.querySelectorAll('[data-task]').forEach(el => el.onclick = () => selectTask(el.dataset.task));
  const waiting = state.actions.filter(a => a.status === 'awaiting_approval');
  const waitingTasks = state.tasks.filter(task => task.status === 'waiting_approval');
  $('#approval-count').textContent = waiting.length + waitingTasks.length || '';
  $('#approval-list').innerHTML = waiting.length || waitingTasks.length ? [
    ...waitingTasks.map(taskApprovalCard), ...waiting.map(actionCard),
  ].join('') : '<div class="empty">Nothing needs approval right now.</div>';
  document.querySelectorAll('[data-approve]').forEach(el => el.onclick = () => openApproval(el.dataset.approve));
  document.querySelectorAll('[data-task-decision]').forEach(el => el.onclick = () => decideTask(el.dataset.taskDecision, el.dataset.decision === 'approve'));
  $('#integration-list').innerHTML = INTEGRATION_PROVIDERS.map(provider => {
    const connection = state.integrations.find(item => item.provider === provider.id);
    if (connection) return `<article class="card"><h3>${escape(connection.display_name || provider.name)}</h3><p>${escape(provider.detail)}</p><p>${badge(connection.policy)} ${badge(connection.health)}</p><p>Scopes: ${escape((connection.granted_scopes || []).join(', ') || 'provider default')}</p>${connection.health !== 'healthy' ? `<button data-connect-integration="${provider.id}">${provider.auth === 'oauth' ? 'Reconnect' : 'Configure token'}</button>` : '<p class="success-text">Connected</p>'}</article>`;
    return `<article class="card"><h3>${escape(provider.name)}</h3><p>${escape(provider.detail)}</p><p>${badge('not_connected')}</p><button data-connect-integration="${provider.id}">Connect</button></article>`;
  }).join('');
  document.querySelectorAll('[data-connect-integration]').forEach(el => el.onclick = () => connectIntegration(el.dataset.connectIntegration));
  $('#device-list').innerHTML = state.devices.length ? state.devices.map(d => `<article class="card"><h3>${escape(d.name)}</h3><p>${badge(d.status)} ${d.last_seen_at ? `Last seen ${new Date(d.last_seen_at).toLocaleString()}` : 'Not yet online'}</p><p>${escape((d.capabilities || []).join(', '))}</p>${d.status === 'active' ? `<button class="danger compact" data-revoke-desktop="${escape(d.id)}">Revoke</button>` : ''}</article>`).join('') : '<div class="empty">No paired desktop executor.</div>';
  $('#cli-device-list').innerHTML = state.cliDevices.length ? state.cliDevices.map(d => `<article class="card"><h3>${escape(d.name)}</h3><p>${d.revoked_at ? badge('revoked') : badge('active')} · ${d.last_seen_at ? `Last seen ${new Date(d.last_seen_at).toLocaleString()}` : 'Not yet used'}</p><p class="muted">Expires ${new Date(d.expires_at).toLocaleString()}</p>${!d.revoked_at ? `<button class="danger compact" data-revoke-cli="${escape(d.id)}">Revoke</button>` : ''}</article>`).join('') : '<div class="empty">No registered CLI devices. Sign in again to register a legacy CLI token.</div>';
  document.querySelectorAll('[data-revoke-desktop]').forEach(el => el.onclick = () => revokeDesktop(el.dataset.revokeDesktop));
  document.querySelectorAll('[data-revoke-cli]').forEach(el => el.onclick = () => revokeCli(el.dataset.revokeCli));
  renderConversations();
  $('#schedule-list').innerHTML = state.schedules.length ? state.schedules.map(schedule => `<article class="card"><h3>${escape(schedule.title)}</h3><p>${escape(schedule.objective.slice(0, 160))}</p><p>${badge(schedule.enabled ? 'enabled' : 'disabled')} · every ${Math.round(schedule.interval_seconds / 60)} minute(s)</p><p class="muted">Next run: ${new Date(schedule.next_run_at).toLocaleString()}</p>${schedule.last_task_id ? `<p class="muted">Last task: ${escape(schedule.last_task_id)}</p>` : ''}${schedule.enabled ? `<button data-cancel-schedule="${schedule.id}" class="secondary">Stop schedule</button>` : ''}</article>`).join('') : '<div class="empty">No schedules yet.</div>';
  $('#research-list').innerHTML = state.research.length ? state.research.map(task => `<article class="card"><h3>${escape(task.title)}</h3><p>${escape(task.objective.slice(0, 180))}</p><p>${badge(task.status)}</p><button data-research-task="${escape(task.id)}" class="secondary">Open evidence</button></article>`).join('') : '<div class="empty">No research runs yet.</div>';
  document.querySelectorAll('[data-research-task]').forEach(el => el.onclick = async () => { document.querySelector('[data-view="tasks"]').click(); await selectTask(el.dataset.researchTask); });
  document.querySelectorAll('[data-cancel-schedule]').forEach(el => el.onclick = () => cancelSchedule(el.dataset.cancelSchedule));
  if (!active.length && state.tasks.length) notice('All current tasks are at a safe terminal state.');
}

async function connectIntegration(provider) {
  if (provider === 'telegram') {
    $('#integration-dialog').showModal();
    return;
  }
  try {
    const result = await api(`/v1/integrations/${encodeURIComponent(provider)}/oauth/start`);
    const popup = window.open(result.authorization_url, `smara-${provider}-oauth`, 'popup,width=620,height=760');
    if (!popup) throw new Error('Allow pop-ups for Smara, then try connecting again.');
    notice(`${provider} authorization opened. Return here after approving it.`);
  } catch (error) { notice(error.message, true); }
}

window.addEventListener('focus', () => {
  if (state.bridgeToken || state.mode === 'development') refresh();
});

$('#integration-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api('/v1/integrations/telegram', { method: 'PUT', body: JSON.stringify({
      display_name: form.display_name.value.trim(), policy: form.policy.value,
      granted_scopes: [], health: 'not_connected',
    }) });
    await api('/v1/integrations/telegram/credential', { method: 'PUT', body: JSON.stringify({
      kind: 'bot_token', secret: form.token.value,
    }) });
    form.token.value = '';
    $('#integration-dialog').close();
    await refresh();
    notice('Telegram connected securely. Sends still require approval.');
  } catch (error) { notice(error.message, true); }
});

function actionCard(action) { return `<article class="card"><h3>${escape(action.action)}</h3><p>${escape(action.preview)}</p><p>${badge(action.status)} · ${escape(action.provider || 'integration')}</p><button data-approve="${action.id}">Review and decide</button></article>`; }
function taskApprovalCard(task) { return `<article class="card"><h3>${escape(task.title)}</h3><p>${escape(task.objective)}</p><p>${badge(task.status)} · durable task</p><div class="actions left"><button data-task-decision="${escape(task.id)}" data-decision="approve">Approve</button><button class="danger" data-task-decision="${escape(task.id)}" data-decision="deny">Deny</button></div></article>`; }

async function decideTask(id, approved) {
  try {
    await api(`/v1/tasks/${id}/approval`, { method: 'POST', body: JSON.stringify({ approved, note: `${approved ? 'Approved' : 'Denied'} in Smara Web.` }) });
    await refresh(); notice(`Task ${approved ? 'approved' : 'denied'}.`);
  } catch (error) { notice(error.message, true); }
}

async function revokeDesktop(id) {
  if (!confirm('Revoke this desktop? It will stop receiving local work immediately.')) return;
  try { await api(`/v1/executors/${encodeURIComponent(id)}`, { method: 'DELETE' }); await refresh(); notice('Desktop revoked.'); } catch (error) { notice(error.message, true); }
}

async function revokeCli(id) {
  if (!confirm('Revoke this CLI device? Its saved login will stop working immediately.')) return;
  try { await api(`/v1/cli/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }); await refresh(); notice('CLI device revoked.'); } catch (error) { notice(error.message, true); }
}
function evidenceCard(item) {
  const flags = Array.isArray(item.quality_flags) && item.quality_flags.length ? item.quality_flags.join(', ') : 'quality checks passed';
  const url = /^https?:\/\//i.test(item.url || '') ? escape(item.url) : '#';
  const published = item.published_at ? ` · published ${escape(item.published_at)}` : '';
  const agreement = item.agreement_count ? ` · agrees with ${item.agreement_count} source(s)` : '';
  return `<li class="evidence-item"><div><a href="${url}" target="_blank" rel="noreferrer">${escape(item.citation_label || 'Source')} — ${escape(item.title || item.url)}</a> ${badge(item.status)}</div><small>${escape(item.domain_policy || 'unclassified')}${published}${agreement}</small><small class="quality">${escape(flags)}</small>${item.error ? `<small class="error-text">${escape(item.error)}</small>` : ''}</li>`;
}
async function loadTaskDetail(id) {
  const detail = $('#task-detail'); detail.classList.remove('empty'); detail.textContent = 'Loading task details…';
  try {
    const [task, steps, events, artifacts, evidence] = await Promise.all([
      api(`/v1/tasks/${id}`), api(`/v1/tasks/${id}/steps`).then(x => x.steps), api(`/v1/tasks/${id}/events`).then(x => x.events), api(`/v1/tasks/${id}/artifacts`).then(x => x), api(`/v1/research/${id}/evidence`).catch(() => []),
    ]);
    detail.innerHTML = `<header><div><p class="eyebrow">${escape(task.workspace_id)}</p><h2>${escape(task.title)}</h2><p class="muted">${escape(task.objective)}</p></div>${badge(task.status)}</header><div class="columns"><section class="panel"><h3>Plan</h3><ul>${steps.map(s => `<li><b>${escape(s.name)}</b> ${badge(s.status)}</li>`).join('') || '<li>No steps recorded.</li>'}</ul></section><section class="panel"><h3>Evidence ledger</h3><p class="muted">Only verified sources are used in the report. Quality flags stay visible for review.</p><ul class="evidence-list">${evidence.map(evidenceCard).join('') || '<li>No research evidence.</li>'}</ul></section><section class="panel"><h3>Artifacts</h3><ul>${artifacts.map(a => `<li><b>${escape(a.name)}</b><br>${escape((a.content || '').slice(0, 180))}</li>`).join('') || '<li>No artifacts.</li>'}</ul></section></div><section class="panel"><h3>Activity</h3><ul class="activity">${events.map(e => `<li>${new Date(e.created_at).toLocaleString()} — ${escape(e.type)}</li>`).join('') || '<li>No events.</li>'}</ul></section>`;
  } catch (error) { detail.textContent = error.message; notice(error.message, true); }
}
async function selectTask(id) {
  state.selected = id; render();
  streamTaskEvents(id);
  await loadTaskDetail(id);
}
async function streamTaskEvents(id) {
  if (state.eventStreamAbort) state.eventStreamAbort.abort();
  const controller = new AbortController(); state.eventStreamAbort = controller;
  let reconnects = 0;
  while (!controller.signal.aborted && reconnects < 8) {
    try {
      const streamHeaders = headers();
      if (state.eventIds[id]) streamHeaders['Last-Event-ID'] = state.eventIds[id];
      const response = await fetch(`/v1/tasks/${id}/events/stream`, { headers: streamHeaders, signal: controller.signal });
      if (response.status === 401 || response.status === 403 || !response.body) return;
      if (!response.ok) throw new Error(`event stream ${response.status}`);
      reconnects = 0;
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      while (!controller.signal.aborted) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n'); buffer = frames.pop() || '';
        for (const frame of frames) {
          const eventId = frame.split('\n').find(item => item.startsWith('id: '));
          if (eventId) state.eventIds[id] = eventId.slice(4);
          const line = frame.split('\n').find(item => item.startsWith('data: ')); if (!line) continue;
          try {
            const payload = JSON.parse(line.slice(6));
            if (frame.includes('event: done')) { notice(`Task ${payload.status}.`); await loadTaskDetail(id); return; }
            if (payload.type) { notice(`Task update: ${payload.type.replaceAll('_', ' ')}`); if (state.selected === id) loadTaskDetail(id); }
          } catch (_) { /* Ignore a partial or malformed progress frame; reconnect/polling remains active. */ }
        }
      }
      if (controller.signal.aborted) return;
      reconnects += 1;
      const delay = Math.min(1_000 * (2 ** (reconnects - 1)), 10_000);
      notice(`Live updates disconnected; reconnecting in ${Math.round(delay / 1000)}s.`, true);
      await new Promise(resolve => setTimeout(resolve, delay));
    } catch (error) {
      if (error.name === 'AbortError' || controller.signal.aborted) return;
      reconnects += 1;
      const delay = Math.min(1_000 * (2 ** (reconnects - 1)), 10_000);
      notice(`Live updates paused; retrying in ${Math.round(delay / 1000)}s.`, true);
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }
  if (!controller.signal.aborted) notice('Live updates unavailable; periodic refresh remains active.', true);
}
function openApproval(id) {
  const action = state.actions.find(a => a.id === id); if (!action) return;
  const form = $('#approval-form'); form.dataset.actionId = id;
  form.preview.value = action.preview;
  form.payload.value = JSON.stringify(typeof action.payload === 'string' ? JSON.parse(action.payload) : action.payload || {}, null, 2);
  form.note.value = ''; $('#approval-dialog').showModal();
}
$('#approval-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; const decision = event.submitter.value;
  try {
    const body = { approved: decision === 'approve', note: form.note.value, edited_preview: form.preview.value, edited_payload: JSON.parse(form.payload.value) };
    await api(`/v1/integration-actions/${form.dataset.actionId}/approval`, { method: 'POST', body: JSON.stringify(body) });
    $('#approval-dialog').close(); await refresh(); notice(`Action ${body.approved ? 'approved' : 'denied'}.`);
  } catch (error) { notice(error.message, true); }
});
$('#new-task').onclick = () => $('#task-dialog').showModal();
$('#new-schedule').onclick = () => $('#schedule-dialog').showModal();
$('#new-research').onclick = () => $('#research-dialog').showModal();
$('#capture').onclick = () => $('#capture-dialog').showModal();
$('#new-chat').onclick = () => {
  if (state.chatAbort) state.chatAbort.abort();
  setConversation(`chat_web_${crypto.randomUUID().replaceAll('-', '')}`);
  $('#chat-messages').innerHTML = '<div class="empty"><h2>What can I help with?</h2><p>Short read-only work happens here. Long or risky work becomes a visible task with approvals.</p></div>';
  $('#chat-progress').textContent = '';
  $('#chat-form').message.focus();
};
$('#chat-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget; const message = form.message.value.trim();
  if (!message) return;
  form.message.value = '';
  await sendChat(message);
});
$('#chat-form').message.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#chat-form').requestSubmit(); }
});
$('#chat-task').onclick = async () => {
  const message = $('#chat-form').message.value.trim();
  if (!message) return notice('Describe the work before creating a task.', true);
  try {
    const task = await api('/v1/tasks', { method: 'POST', body: JSON.stringify({
      title: message.length > 90 ? `${message.slice(0, 87)}…` : message,
      objective: message, workspace_id: 'default', requires_approval: true,
      steps: [{ name: 'agent.execute' }],
    }) });
    $('#chat-form').message.value = '';
    await refresh();
    document.querySelectorAll('.nav,.view').forEach(el => el.classList.remove('active'));
    document.querySelector('[data-view="tasks"]').classList.add('active'); $('#tasks').classList.add('active'); $('#title').textContent = 'Tasks';
    await selectTask(task.id); notice('Durable task created. Review its plan and approval before execution.');
  } catch (error) { notice(error.message, true); }
};
$('#task-form').addEventListener('submit', async event => {
  event.preventDefault(); if (event.submitter.value !== 'submit') return $('#task-dialog').close(); const form = event.currentTarget;
  try { await api('/v1/tasks', { method: 'POST', body: JSON.stringify({ title: form.title.value, objective: form.objective.value, workspace_id: form.workspace.value, requires_approval: form.approval.checked, steps: [{ name: 'agent.execute' }] }) }); $('#task-dialog').close(); form.reset(); await refresh(); notice('Task created.'); } catch (error) { notice(error.message, true); }
});
$('#schedule-form').addEventListener('submit', async event => {
  event.preventDefault(); if (event.submitter.value !== 'submit') return $('#schedule-dialog').close(); const form = event.currentTarget;
  try { await api('/v1/schedules', { method: 'POST', body: JSON.stringify({ title: form.title.value, objective: form.objective.value, workspace_id: form.workspace.value, interval_seconds: Number(form.interval.value) * 60, requires_approval: form.approval.checked, steps: [{ name: 'agent.execute' }] }) }); $('#schedule-dialog').close(); form.reset(); await refresh(); notice('Schedule created.'); } catch (error) { notice(error.message, true); }
});
$('#research-form').addEventListener('submit', async event => {
  event.preventDefault(); if (event.submitter.value !== 'submit') return $('#research-dialog').close(); const form = event.currentTarget;
  try {
    const sources = form.sources.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    const task = await api('/v1/research', { method: 'POST', body: JSON.stringify({ title: form.title.value, question: form.question.value, workspace_id: form.workspace.value, sources }) });
    $('#research-dialog').close(); form.reset(); await refresh(); document.querySelector('[data-view="research"]').click(); notice(`Research started: ${task.title}`);
  } catch (error) { notice(error.message, true); }
});
async function cancelSchedule(id) {
  try { await api(`/v1/schedules/${id}`, { method: 'DELETE' }); await refresh(); notice('Schedule stopped.'); } catch (error) { notice(error.message, true); }
}
$('#pair-desktop').onclick = async () => { try { const pairing = await api('/v1/executors/pairings', { method: 'POST', body: JSON.stringify({ name: 'My desktop', capabilities: ['local_file_read'] }) }); $('#device-list').innerHTML = `<article class="card"><h3>Pair this desktop</h3><p>Run <code>smara-desktop --pair ${pairing.code} --api ${location.origin} --allow-root &lt;folder&gt;</code> once in PowerShell. This one-time code expires at ${new Date(pairing.expires_at).toLocaleTimeString()}.</p><p class="pair-code">${pairing.code}</p><p class="muted">Only local file reads are enabled by this pairing. Add terminal or browser capabilities only with a separate, reviewed pairing.</p></article>`; } catch (error) { notice(error.message, true); } };
$('#pair-cli').onclick = async () => {
  try {
    const pairing = await api('/v1/cli/device/start', { method: 'POST', body: JSON.stringify({ name: 'Smara CLI' }) });
    $('#cli-code').textContent = pairing.code;
    $('#cli-expires').textContent = `Expires at ${new Date(pairing.expires_at).toLocaleTimeString()}. Do not share this code.`;
    $('#cli-dialog').showModal();
  } catch (error) { notice(error.message, true); }
};
$('#cli-approve').onclick = async () => {
  if (!state.cliDeviceCode) return;
  try {
    await api('/v1/cli/device/authorize', { method: 'POST', body: JSON.stringify({ device_code: state.cliDeviceCode }) });
    $('#cli-dialog').close();
    history.replaceState({}, '', `${location.pathname}${location.hash}`);
    state.cliDeviceCode = null;
    notice('CLI device approved. Return to your terminal.');
  } catch (error) { notice(error.message, true); }
};
$('#capture-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; if (event.submitter.value !== 'submit') return $('#capture-dialog').close();
  try {
    const media = form.media.files[0]; const data = new FormData(); data.append('title', form.title.value);
    if (media) { data.append('file', media); await api('/v1/captures/media', { method: 'POST', body: data }); }
    else { data.append('text', form.text.value); await api('/v1/captures/text', { method: 'POST', body: data }); }
    $('#capture-dialog').close(); form.reset(); await refresh(); notice('Capture saved to your inbox.');
  } catch (error) { notice(error.message, true); }
});
function urlBase64ToUint8Array(value) { const raw = atob(value.replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from(raw, char => char.charCodeAt(0)); }
$('#enable-push').onclick = async () => {
  try {
    const key = (await api('/v1/push/public-key')).public_key;
    if (!key) throw new Error('Phone alerts are not configured on this server yet.');
    const registration = await navigator.serviceWorker.ready;
    const permission = await Notification.requestPermission(); if (permission !== 'granted') throw new Error('Notification permission was not granted.');
    const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) });
    const json = subscription.toJSON(); await api('/v1/push/subscriptions', { method: 'POST', body: JSON.stringify({ endpoint: json.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth }) });
    const result = await api('/v1/push/test', { method: 'POST', body: '{}' }); notice(result.delivered ? 'Phone alert sent.' : 'Phone subscription saved; server VAPID delivery is not active yet.');
  } catch (error) { notice(error.message, true); }
};
$('#refresh').onclick = refresh;
document.querySelectorAll('.nav').forEach(button => button.onclick = () => { document.querySelectorAll('.nav,.view').forEach(el => el.classList.remove('active')); button.classList.add('active'); $(`#${button.dataset.view}`).classList.add('active'); $('#title').textContent = button.textContent.trim().replace(/\d+$/, '').trim(); });
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/app/sw.js').catch(() => {});
refresh(); setInterval(refresh, 5000);
