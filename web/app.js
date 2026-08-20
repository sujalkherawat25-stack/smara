const state = { tasks: [], actions: [], selected: null, mode: 'development' };
const $ = (selector) => document.querySelector(selector);
const account = $('#account');
account.value = localStorage.getItem('smara-account') || account.value;
account.addEventListener('change', () => { localStorage.setItem('smara-account', account.value.trim()); refresh(); });

function headers() {
  const result = {};
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
    $('#auth-mode').textContent = state.mode === 'development' ? 'Development account' : 'Signed-in gateway';
    [state.tasks, state.actions, state.integrations, state.devices] = await Promise.all([
      api('/v1/tasks'), api('/v1/integration-actions').then(x => x.actions), api('/v1/integrations').then(x => x.integrations), api('/v1/executors').then(x => x.executors),
    ]);
    render();
    notice(`Updated ${new Date().toLocaleTimeString()}`);
  } catch (error) { notice(error.message, true); }
}
function render() {
  $('#task-total').textContent = state.tasks.length;
  const active = state.tasks.filter(t => !['completed', 'failed', 'cancelled'].includes(t.status));
  $('#task-list').innerHTML = state.tasks.length ? state.tasks.map(task => `<article class="task ${task.id === state.selected ? 'selected' : ''}" data-task="${task.id}"><div><h3>${escape(task.title)}</h3><p>${escape(task.objective.slice(0, 140))}</p></div>${badge(task.status)}</article>`).join('') : `<div class="empty">No tasks yet. Create work you want Smara to coordinate.</div>`;
  document.querySelectorAll('[data-task]').forEach(el => el.onclick = () => selectTask(el.dataset.task));
  const waiting = state.actions.filter(a => a.status === 'awaiting_approval');
  $('#approval-count').textContent = waiting.length ? waiting.length : '';
  $('#approval-list').innerHTML = waiting.length ? waiting.map(actionCard).join('') : '<div class="empty">Nothing needs approval right now.</div>';
  document.querySelectorAll('[data-approve]').forEach(el => el.onclick = () => openApproval(el.dataset.approve));
  $('#integration-list').innerHTML = state.integrations.length ? state.integrations.map(i => `<article class="card"><h3>${escape(i.display_name || i.provider)}</h3><p>${badge(i.policy)} ${badge(i.health)}</p><p>Scopes: ${escape((i.granted_scopes || []).join(', ') || 'none')}</p></article>`).join('') : '<div class="empty">No integrations configured.</div>';
  $('#device-list').innerHTML = state.devices.length ? state.devices.map(d => `<article class="card"><h3>${escape(d.name)}</h3><p>${badge(d.status)} ${d.last_seen_at ? `Last seen ${new Date(d.last_seen_at).toLocaleString()}` : 'Not yet online'}</p><p>${escape((d.capabilities || []).join(', '))}</p></article>`).join('') : '<div class="empty">No paired desktop executor.</div>';
  if (!active.length && state.tasks.length) notice('All current tasks are at a safe terminal state.');
}
function actionCard(action) { return `<article class="card"><h3>${escape(action.action)}</h3><p>${escape(action.preview)}</p><p>${badge(action.status)} · ${escape(action.provider || 'integration')}</p><button data-approve="${action.id}">Review and decide</button></article>`; }
async function selectTask(id) {
  state.selected = id; render();
  const detail = $('#task-detail'); detail.classList.remove('empty'); detail.textContent = 'Loading task details…';
  try {
    const [task, steps, events, artifacts, evidence] = await Promise.all([
      api(`/v1/tasks/${id}`), api(`/v1/tasks/${id}/steps`).then(x => x.steps), api(`/v1/tasks/${id}/events`).then(x => x.events), api(`/v1/tasks/${id}/artifacts`).then(x => x), api(`/v1/research/${id}/evidence`).catch(() => []),
    ]);
    detail.innerHTML = `<header><div><p class="eyebrow">${escape(task.workspace_id)}</p><h2>${escape(task.title)}</h2><p class="muted">${escape(task.objective)}</p></div>${badge(task.status)}</header><div class="columns"><section class="panel"><h3>Plan</h3><ul>${steps.map(s => `<li><b>${escape(s.name)}</b> ${badge(s.status)}</li>`).join('') || '<li>No steps recorded.</li>'}</ul></section><section class="panel"><h3>Evidence</h3><ul>${evidence.map(e => `<li>${escape(e.citation_label || 'Source')} — ${escape(e.title || e.url)} ${badge(e.status)}</li>`).join('') || '<li>No research evidence.</li>'}</ul></section><section class="panel"><h3>Artifacts</h3><ul>${artifacts.map(a => `<li><b>${escape(a.name)}</b><br>${escape((a.content || '').slice(0, 180))}</li>`).join('') || '<li>No artifacts.</li>'}</ul></section></div><section class="panel"><h3>Activity</h3><ul class="activity">${events.map(e => `<li>${new Date(e.created_at).toLocaleString()} — ${escape(e.type)}</li>`).join('') || '<li>No events.</li>'}</ul></section>`;
  } catch (error) { detail.textContent = error.message; notice(error.message, true); }
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
$('#capture').onclick = () => $('#capture-dialog').showModal();
$('#task-form').addEventListener('submit', async event => {
  event.preventDefault(); if (event.submitter.value !== 'submit') return $('#task-dialog').close(); const form = event.currentTarget;
  try { await api('/v1/tasks', { method: 'POST', body: JSON.stringify({ title: form.title.value, objective: form.objective.value, workspace_id: form.workspace.value, requires_approval: form.approval.checked, steps: [{ name: 'execute_task' }] }) }); $('#task-dialog').close(); form.reset(); await refresh(); notice('Task created.'); } catch (error) { notice(error.message, true); }
});
$('#pair-desktop').onclick = async () => { try { const pairing = await api('/v1/executors/pairings', { method: 'POST', body: JSON.stringify({ name: 'My desktop', capabilities: ['local_file_read'] }) }); $('#device-list').innerHTML = `<article class="card"><h3>Pair this desktop</h3><p>Run the Memento bridge command with this one-time code. It expires at ${new Date(pairing.expires_at).toLocaleTimeString()}.</p><p class="pair-code">${pairing.code}</p></article>`; } catch (error) { notice(error.message, true); } };
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
