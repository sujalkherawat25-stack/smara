async function show() {
  const { status } = await chrome.storage.session.get("status");
  const value = status || {};
  const state = document.getElementById("state");
  if (value.paused) { state.textContent = "Paused: Smara will not use this browser."; state.className = "off"; }
  else if (value.host) { state.textContent = "Connected to Smara Desktop."; state.className = "ok"; }
  else { state.textContent = "Smara Desktop is not set up on this PC yet (Settings > Browser)."; state.className = "off"; }
  const count = (value.tabs || []).length;
  document.getElementById("tabs").textContent = count ? `Working in ${count} tab${count > 1 ? "s" : ""}.` : "";
  document.getElementById("pause").textContent = value.paused ? "Let Smara use this browser again" : "Pause Smara in this browser";
  document.getElementById("pause").onclick = async () => {
    await chrome.runtime.sendMessage({ type: "pause", paused: !value.paused });
    show();
  };
}
show();
