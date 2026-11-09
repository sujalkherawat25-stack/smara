//! Smara's native-source runtime transport. No model or tool loop lives here.
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Stdio};
use std::sync::mpsc::{self, Sender};
use std::sync::{Arc, Mutex, OnceLock};
use std::collections::{HashMap, HashSet};
use tauri::{AppHandle, Emitter};

struct NativeSession {
    process: Child,
    /// Writes go through a dedicated writer thread, so native_send never blocks on the runtime's pipe.
    input: Option<Sender<String>>,
    generation: String,
    activity: Arc<Mutex<ReviewActivity>>,
    /// The model profile and its key the runtime was started with; a change to either needs a restart.
    profile_id: String,
    credential_name: String,
}

/// Owns the runtime's stdin. Lines queue here in order; dropping every sender ends the thread, which drops stdin,
/// which the runtime reads as end-of-input and shuts down.
fn spawn_writer<W: Write + Send + 'static>(mut output: W) -> Sender<String> {
    let (tx, rx) = mpsc::channel::<String>();
    std::thread::spawn(move || {
        for line in rx {
            if writeln!(output, "{line}").and_then(|_| output.flush()).is_err() { break; }
        }
    });
    tx
}

/// Whether a settings operation changes what the running runtime was started with. The UI calls this rule
/// too (src/nativeSettingsPolicy.ts); keep the two in step.
pub fn needs_runtime_restart(operation: &str, request: &Value, running: Option<(&str, &str)>) -> bool {
    let Some((active_profile, active_credential)) = running else { return false };
    match operation {
        "select_model" | "select_project" | "save_project_options" => true,
        "save_model" => request["profile"]["id"].as_str() == Some(active_profile),
        "save_credential" | "delete_credential" => request["name"].as_str() == Some(active_credential),
        _ => false,
    }
}

/// Profile and key of the live runtime, if one is running.
pub fn running_profile() -> Option<(String, String)> {
    let mut lock = session().lock().ok()?;
    let running = lock.as_mut()?;
    if running.process.try_wait().ok()?.is_some() { return None; }
    Some((running.profile_id.clone(), running.credential_name.clone()))
}

#[derive(Default)]
struct ReviewActivity {
    pending: HashSet<String>,
    turns: HashMap<String, String>,
    workers: HashSet<String>,
    completed: HashSet<String>,
    readiness_requests: HashSet<String>,
    terminal_processes: HashSet<String>,
    terminal_exec_requests: HashMap<String, String>,
    windows_sandbox_ready: bool,
    unknown: bool,
}

/// Unknown models are assumed to have a 128k window so automatic compaction (at 80%) always engages; an unset limit never compacts and the chat dies when full.
pub const DEFAULT_CONTEXT_WINDOW: u64 = 128_000;

impl ReviewActivity {
    fn collaboration(&mut self, item: &Value) {
        if item["type"] != "collabAgentToolCall" { return; }
        if let Some(receivers) = item["receiverThreadIds"].as_array() {
            for receiver in receivers {
                if let Some(thread) = receiver.as_str() {
                    match item["agentsStates"][thread]["status"].as_str() {
                        Some("completed" | "failed" | "shutdown" | "notFound") => { self.workers.remove(thread); }
                        // Tool completion alone does not mean worker completion.
                        _ => { self.workers.insert(thread.into()); }
                    }
                } else { self.unknown = true; }
            }
        }
    }
    fn request(&mut self, message: &Value) {
        if let Some(id) = message.get("id") {
            let request_id = id.to_string();
            match message["method"].as_str() {
                Some("turn/start" | "thread/resume") => { self.pending.insert(request_id); }
                Some("windowsSandbox/readiness") => { self.readiness_requests.insert(request_id); }
                Some("command/exec") => {
                    self.pending.insert(request_id.clone());
                    if let Some(process_id) = message["params"]["processId"].as_str() {
                        self.terminal_processes.insert(process_id.to_owned());
                        self.terminal_exec_requests.insert(request_id, process_id.to_owned());
                    } else { self.unknown = true; }
                }
                _ => {}
            }
        } else if matches!(message["method"].as_str(), Some("turn/start" | "thread/resume" | "windowsSandbox/readiness" | "command/exec")) {
            self.unknown = true;
        }
    }
    fn notification(&mut self, message: &Value) {
        if message.get("method").is_none() {
            if let Some(id) = message.get("id") {
                let request_id = id.to_string();
                self.pending.remove(&request_id);
                if self.readiness_requests.remove(&request_id) {
                    self.windows_sandbox_ready = message["result"]["status"] == "ready";
                }
                if let Some(process_id) = self.terminal_exec_requests.remove(&request_id) {
                    self.terminal_processes.remove(&process_id);
                }
            }
            // Resuming an in-progress session must not enable Git writes.
            if let Some(turns) = message["result"]["thread"]["turns"].as_array() {
                for turn in turns {
                    if let Some(items) = turn["items"].as_array() {
                        for item in items { self.collaboration(item); }
                    }
                    if turn["status"] == "inProgress" {
                        if let (Some(thread), Some(id)) = (message["result"]["thread"]["id"].as_str(), turn["id"].as_str()) {
                            self.turns.insert(thread.into(), id.into());
                        } else { self.unknown = true; }
                    }
                }
            }
            // A turn/start response can precede turn/started.
            if message["result"]["turn"]["status"] == "inProgress" {
                let id = message["result"]["turn"]["id"].as_str().unwrap_or("unknown");
                if !self.completed.contains(id) { self.turns.entry(format!("pending-turn-{id}")).or_insert(id.into()); }
            }
        }
        if message["method"] == "item/started" || message["method"] == "item/completed" {
            self.collaboration(&message["params"]["item"]);
        }
        if message["method"] == "turn/started" || message["method"] == "turn/completed" {
            if let (Some(thread), Some(id)) = (message["params"]["threadId"].as_str(), message["params"]["turn"]["id"].as_str()) {
                self.turns.remove(&format!("pending-turn-{id}"));
                if message["method"] == "turn/started" { self.turns.insert(thread.into(), id.into()); }
                else {
                    self.completed.insert(id.into());
                    self.workers.remove(thread);
                    if self.turns.get(thread).is_some_and(|active| active == id) { self.turns.remove(thread); }
                }
            } else { self.unknown = true; }
        }
    }
    fn idle(&self) -> bool { !self.unknown && self.pending.is_empty() && self.turns.is_empty() && self.workers.is_empty() }
}

fn valid_terminal_process_id(value: &str) -> bool {
    value.starts_with("smara-terminal-") && value.len() <= 128
        && value.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn path_is_exact_root(value: &Value, root: &std::path::Path) -> bool {
    value.as_str().is_some_and(|value| {
        let path = std::path::PathBuf::from(value);
        path.is_absolute() && std::fs::canonicalize(path).is_ok_and(|path| path == root)
    })
}

// The actual MXC command/exec gate passed on 2026-10-14, including TCP/UDP/
// IPv6, descendants, junction writes and PTY lifecycle. Python explicitly
// selects MXC; unsupported hosts must fail rather than downgrade backends.
const WINDOWS_SANDBOX_STREAMING_SUPPORTED: bool = cfg!(target_os = "windows");

fn validate_terminal_message(message: &Value, activity: &ReviewActivity, project_root: Option<&std::path::Path>) -> Result<(), String> {
    let params = &message["params"];
    match message["method"].as_str().unwrap_or_default() {
        "command/exec" => {
            if !WINDOWS_SANDBOX_STREAMING_SUPPORTED {
                return Err("Interactive terminal requires the verified Windows MXC backend. No shell was started.".into());
            }
            if !activity.windows_sandbox_ready { return Err("Windows sandbox readiness has not been confirmed for this runtime.".into()); }
            let root = project_root.ok_or("The selected project is unavailable for the terminal sandbox.")?;
            if !valid_terminal_process_id(params["processId"].as_str().unwrap_or_default())
                || activity.terminal_processes.contains(params["processId"].as_str().unwrap_or_default())
                || params["command"] != serde_json::json!(["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command",
                    "if (Get-Module PSReadLine) { Remove-Module PSReadLine -ErrorAction Stop }"])
                || params["tty"] != true || params["streamStdin"] != true || params["streamStdoutStderr"] != true
                || params["disableTimeout"] != true || params["outputBytesCap"] != 8 * 1024 * 1024
                || !params["env"].is_null() || !params["permissionProfile"].is_null()
                || params["sandboxPolicy"]["type"] != "workspaceWrite"
                || params["sandboxPolicy"]["networkAccess"] != false
                || !path_is_exact_root(&params["cwd"], root)
            { return Err("Terminal request does not match Smara's restricted interactive-shell policy.".into()); }
            let roots = params["sandboxPolicy"]["writableRoots"].as_array().ok_or("Terminal writable roots are invalid.")?;
            if roots.len() != 1 || !path_is_exact_root(&roots[0], root) {
                return Err("The terminal can write only to the currently selected project.".into());
            }
            let cols = params["size"]["cols"].as_u64().unwrap_or_default();
            let rows = params["size"]["rows"].as_u64().unwrap_or_default();
            if !(40..=180).contains(&cols) || !(10..=60).contains(&rows) {
                return Err("Terminal dimensions are outside the allowed range.".into());
            }
            Ok(())
        }
        "command/exec/write" => {
            let id = params["processId"].as_str().unwrap_or_default();
            if !valid_terminal_process_id(id) || !activity.terminal_processes.contains(id)
                || params["closeStdin"] == true
                || params["deltaBase64"].as_str().is_none_or(|value| value.is_empty() || value.len() > 131_072)
            { return Err("Terminal input is invalid or the shell is no longer active.".into()); }
            Ok(())
        }
        "command/exec/resize" | "command/exec/terminate" => {
            let id = params["processId"].as_str().unwrap_or_default();
            if !valid_terminal_process_id(id) || !activity.terminal_processes.contains(id) {
                return Err("Terminal process is not owned by this Smara session.".into());
            }
            if message["method"] == "command/exec/resize" {
                let cols = params["size"]["cols"].as_u64().unwrap_or_default();
                let rows = params["size"]["rows"].as_u64().unwrap_or_default();
                if !(40..=180).contains(&cols) || !(10..=60).contains(&rows) {
                    return Err("Terminal dimensions are outside the allowed range.".into());
                }
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

fn session() -> &'static Mutex<Option<NativeSession>> {
    static SESSION: OnceLock<Mutex<Option<NativeSession>>> = OnceLock::new();
    SESSION.get_or_init(|| Mutex::new(None))
}

fn binary() -> Result<PathBuf, String> {
    let packaged = super::executor_executable().parent()
        .map(|path| path.join("native").join("smara-native.exe"));
    let checkout = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../native/dist/smara-native.exe");
    if let Some(path) = packaged.filter(|path| path.is_file()) { return Ok(path); }
    if cfg!(debug_assertions) && checkout.is_file() { return Ok(checkout); }
    Err("The copied Smara native runtime is not built/bundled. Build scripts/build-smara-native.ps1. Installed Codex and legacy agent fallback are disabled.".into())
}

#[tauri::command]
pub fn native_status() -> Value {
    match binary() {
        Ok(path) => json!({"built": true, "binary": path, "sourceOwned": true, "windowsTerminalBuild": WINDOWS_SANDBOX_STREAMING_SUPPORTED}),
        Err(error) => json!({"built": false, "error": error, "sourceOwned": true, "windowsTerminalBuild": WINDOWS_SANDBOX_STREAMING_SUPPORTED}),
    }
}

fn start_session(app: AppHandle, workspace: String, profile_id: String, tools_enabled: Option<bool>, workers_enabled: Option<bool>, browser_origins: Option<Vec<String>>) -> Result<Value, String> {
    {
        let mut lock = session().lock().map_err(|_| "Native session lock failed")?;
        if let Some(running) = lock.as_mut() {
            if running.process.try_wait().map_err(|_| "Cannot inspect native process")?.is_none() {
                return Err("Stop the current native runtime before changing workspace or model.".into());
            }
            // Reap a crashed transport before reconnecting; do not strand the UI
            // behind an already-exited global session.
            *lock = None;
        }
    }
    // Setup below (settings, key lookup, process start) runs without the session lock, so native_send
    // and native_stop never queue behind it. The install at the end re-checks for a racing start.
    let native_binary = binary()?;
    let connection = super::current_connection()?;   // cached snapshot, not a fresh settings read
    let root = super::selected_workspace_dir(&workspace, &connection.allowed_roots)?
        .ok_or("Choose an existing workspace folder")?;
    let mut matches = super::stored_local_model_profiles()?.into_iter()
        .filter(|profile| profile.id == profile_id).collect::<Vec<_>>();
    if matches.len() != 1 { return Err("Select one unambiguous configured model in Settings".into()); }
    let profile = matches.remove(0);
    let secret = super::resolve_local_secret(&profile.credential_name)?;
    let generation = format!("native-{}", chrono::Utc::now().timestamp_nanos_opt().unwrap_or_default());
    let mut command = super::executor_command(&["--native-app-server".into()]);
    // Metadata requests intentionally discard stderr. The long-lived
    // transport instead needs its own pipe, drained below without logging
    // secrets or mistaking stderr for native protocol output.
    command.stdin(Stdio::piped()).stderr(Stdio::piped()).current_dir(&root);
    let mut process = command.spawn().map_err(|error| format!("Cannot start native transport: {error}"))?;
    let pipes = (process.stdin.take(), process.stdout.take(), process.stderr.take());
    let (Some(mut input), Some(output), Some(errors)) = pipes else {
        let _ = process.kill(); let _ = process.wait();
        return Err("Native transport stdio pipes are missing".into());
    };
    let config = json!({"binary": native_binary, "workspace": root,
        "home": super::state_path().parent().unwrap_or(std::path::Path::new(".")).join("native-runtime"), "model": profile.model,
        "base_url": profile.base_url, "api_key": secret, "auth_header": profile.auth_header, "context_window": profile.context_window.or(Some(DEFAULT_CONTEXT_WINDOW)),
        "tools_enabled": tools_enabled.unwrap_or(false), "workers_enabled": workers_enabled.unwrap_or(false), "browser_origins": browser_origins.unwrap_or_default()});
    if writeln!(input, "{config}").and_then(|_| input.flush()).is_err() {
        let _ = process.kill(); let _ = process.wait();
        return Err("Cannot configure native transport".into());
    }
    let event_generation = generation.clone();
    let activity = Arc::new(Mutex::new(ReviewActivity::default()));
    let event_activity = Arc::clone(&activity);
    std::thread::spawn(move || {
        for line in BufReader::new(output).lines() {
            let Ok(line) = line else { break; };
            match serde_json::from_str::<Value>(&line) {
                Ok(message) => {
                    if let Ok(mut active) = event_activity.lock() { active.notification(&message); }
                    let _ = app.emit("native-runtime-event", json!({"generation": event_generation, "message": message}));
                }
                Err(_) => {
                    if let Ok(mut active) = event_activity.lock() { active.unknown = true; }
                    let _ = app.emit("native-runtime-event", json!({"generation": event_generation, "message": {"method": "smara/runtimeError", "params": {"message": "Native runtime returned invalid protocol data"}}}));
                }
            }
        }
        let _ = app.emit("native-runtime-event", json!({"generation": event_generation, "message": {"method": "smara/runtimeStopped"}}));
    });
    std::thread::spawn(move || { for _line in BufReader::new(errors).lines() {} });
    let writer = spawn_writer(input);
    let mut lock = session().lock().map_err(|_| "Native session lock failed")?;
    if let Some(running) = lock.as_mut() {
        if running.process.try_wait().map_err(|_| "Cannot inspect native process")?.is_none() {
            // Another start won the race while this one was spawning: keep the live runtime, stop this one.
            let _ = process.kill(); let _ = process.wait();
            return Err("Stop the current native runtime before changing workspace or model.".into());
        }
    }
    *lock = Some(NativeSession { process, input: Some(writer), generation: generation.clone(), activity,
        profile_id: profile.id.clone(), credential_name: profile.credential_name.clone() });
    Ok(json!({"generation": generation, "sourceOwned": true}))
}

#[tauri::command]
pub async fn native_start(app: AppHandle, workspace: String, profile_id: String, tools_enabled: Option<bool>, workers_enabled: Option<bool>, browser_origins: Option<Vec<String>>) -> Result<Value, String> {
    // Frozen settings/DPAPI/process startup must not block the GUI thread.
    tauri::async_runtime::spawn_blocking(move || start_session(app, workspace, profile_id, tools_enabled, workers_enabled, browser_origins))
        .await.map_err(|_| "Native startup worker failed".to_owned())?
}

/// Validates and enqueues one RPC line. It never starts a process or reads settings from disk on the send path:
/// terminal requests use the cached project snapshot, and the runtime's stdin is written by its writer thread.
#[tauri::command]
pub async fn native_send(generation: String, message: Value) -> Result<(), String> {
    if !message.is_object() { return Err("RPC message must be an object".into()); }
    let snapshot = if message["method"] == "command/exec" { Some(super::cached_snapshot_or_refresh()?) } else { None };
    let mut lock = session().lock().map_err(|_| "Native session lock failed")?;
    let running = lock.as_mut().ok_or("Native runtime is not connected")?;
    if running.generation != generation { return Err("Stale native runtime generation".into()); }
    if running.process.try_wait().map_err(|_| "Cannot inspect native process")?.is_some() {
        return Err("Native process exited; reconnect before sending work".into());
    }
    let mut activity = running.activity.lock().map_err(|_| "Native activity lock failed")?;
    let project_root = match &snapshot {
        Some(snap) => Some(super::selected_workspace_dir(&snap.connection.active_workspace, &snap.connection.allowed_roots)?
            .ok_or("Choose a project before starting a terminal")?),
        None => None,
    };
    validate_terminal_message(&message, &activity, project_root.as_deref())?;
    let input = running.input.as_ref().ok_or("Native runtime is stopping")?;
    activity.request(&message);
    input.send(message.to_string()).map_err(|_| "Cannot send native RPC message".into())
}

fn stop_session(expected_generation: Option<&str>) -> Result<(), String> {
    let mut lock = session().lock().map_err(|_| "Native session lock failed")?;
    if let (Some(expected), Some(running)) = (expected_generation, lock.as_ref()) {
        if running.generation != expected {
            return Err("Stale client cannot stop another native runtime".into());
        }
    }
    // Retain process ownership until termination is confirmed. Losing the
    // session on an inspection/kill failure would make cleanup retry a no-op
    // while permitting a second runtime to be started.
    if let Some(running) = lock.as_mut() {
        if let Some(input) = running.input.take() {
            // The writer sends the shutdown line, then drops stdin (EOF) once the queue drains.
            let _ = input.send("{\"method\":\"smara/shutdown\"}".to_owned());
            drop(input);
        }
        // Allow the native EOF cleanup (bounded to 45s by the bootstrap) to finish before killing its transport
        // process. Stopping sooner could leave workers or Windows-sandbox cleanup unfinished. This wait runs on
        // a blocking worker (native_stop), and the UI stays locked while a restart is in progress.
        for _ in 0..550 {
            if running.process.try_wait().map_err(|_| "Cannot inspect stopping process")?.is_some() {
                *lock = None;
                return Ok(());
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        let pid = running.process.id();
        running.process.kill().map_err(|_| "Could not stop native transport")?;
        running.process.wait().map_err(|_| "Could not reap native transport")?;
        log_line(&format!("native runtime pid {pid} did not exit within 55 s; killed it"));
        *lock = None;
    }
    Ok(())
}

/// Appends one diagnostic line to the Desktop log. Only ids and timings go here, never keys or message text.
fn log_line(message: &str) {
    let folder = super::state_path().parent().map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
    let _ = std::fs::create_dir_all(&folder);
    let path = folder.join("smara-desktop-runtime.log");
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{} {message}", chrono::Utc::now().to_rfc3339());
    }
}

#[tauri::command]
pub async fn native_stop(generation: String) -> Result<(), String> {
    // Waiting for native process cleanup must not freeze the Desktop UI.
    tauri::async_runtime::spawn_blocking(move || stop_session(Some(&generation)))
        .await.map_err(|_| "Native shutdown worker failed".to_string())?
}

pub fn stop_current() -> Result<(), String> {
    // Only application exit, not a client RPC, can stop without generation.
    stop_session(None)
}

pub fn with_disconnected<T>(operation: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
    // Hold the same lock as native_start to close the check/start race.
    let mut lock = session().lock().map_err(|_| "Native session lock failed")?;
    if let Some(running) = lock.as_mut() {
        if running.process.try_wait().map_err(|_| "Cannot inspect native process")?.is_none() {
            return Err("Disconnect before changing project, model or tool settings".into());
        }
    }
    operation()
}

pub fn with_review_idle<T>(operation: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
    // Serialize with native_start/native_send so a turn cannot race a Git write.
    let mut lock = session().lock().map_err(|_| "Native session lock failed")?;
    if let Some(running) = lock.as_mut() {
        if running.process.try_wait().map_err(|_| "Cannot inspect native process")?.is_none() {
            let active = running.activity.lock().map_err(|_| "Native activity lock failed")?;
            if !active.idle() { return Err("Finish or stop all native turns and workers before changing Git files".into()); }
            return operation();
        }
    }
    operation()
}

#[cfg(test)]
mod review_tests {
    use super::*;
    #[test]
    fn pending_start_and_workers_block_review_writes() {
        let mut active = ReviewActivity::default();
        active.request(&json!({"id":1,"method":"turn/start"}));
        assert!(!active.idle());
        active.notification(&json!({"id":1,"result":{"turn":{"id":"parent","status":"inProgress"}}}));
        assert!(!active.idle());
        for (thread, id) in [("p", "parent"), ("w", "worker")] {
            active.notification(&json!({"method":"turn/started","params":{"threadId":thread,"turn":{"id":id}}}));
        }
        active.notification(&json!({"method":"turn/completed","params":{"threadId":"p","turn":{"id":"parent"}}}));
        assert!(!active.idle());
        active.notification(&json!({"method":"turn/completed","params":{"threadId":"w","turn":{"id":"worker"}}}));
        assert!(active.idle());
    }
    #[test]
    fn resumed_active_and_unknown_work_cannot_enable_writes() {
        let mut active = ReviewActivity::default();
        active.notification(&json!({"id":3,"result":{"thread":{"id":"p","turns":[{"id":"t","status":"inProgress"}]}}}));
        assert!(!active.idle());
        active.notification(&json!({"method":"turn/completed","params":{"threadId":"p","turn":{"id":"t"}}}));
        assert!(active.idle());
        active.request(&json!({"method":"turn/start"}));
        assert!(!active.idle());
    }
    #[test]
    fn delayed_start_ack_cannot_relock_a_completed_turn() {
        let mut active = ReviewActivity::default();
        active.request(&json!({"id":1,"method":"turn/start"}));
        active.notification(&json!({"method":"turn/started","params":{"threadId":"p","turn":{"id":"t"}}}));
        active.notification(&json!({"method":"turn/completed","params":{"threadId":"p","turn":{"id":"t"}}}));
        active.notification(&json!({"id":1,"result":{"turn":{"id":"t","status":"inProgress"}}}));
        assert!(active.idle());
    }
    #[test]
    fn collaboration_pending_worker_blocks_after_parent_finishes() {
        let mut active = ReviewActivity::default();
        active.notification(&json!({"method":"item/completed","params":{"item":{
            "type":"collabAgentToolCall","status":"completed","receiverThreadIds":["w"],
            "agentsStates":{"w":{"status":"pendingInit"}}
        }}}));
        active.notification(&json!({"method":"turn/completed","params":{"threadId":"p","turn":{"id":"parent"}}}));
        assert!(!active.idle());
        active.notification(&json!({"method":"item/completed","params":{"item":{
            "type":"collabAgentToolCall","receiverThreadIds":["w"],"agentsStates":{"w":{"status":"completed"}}
        }}}));
        assert!(active.idle());
    }
    #[test]
    fn interactive_terminal_keeps_git_writes_locked_until_its_process_exits() {
        let mut active = ReviewActivity::default();
        active.request(&json!({"id": 9, "method": "command/exec", "params": {"processId": "terminal-1"}}));
        assert!(!active.idle());
        active.notification(&json!({"id": 9, "result": {"exitCode": 0, "stdout": "", "stderr": ""}}));
        assert!(active.idle());
    }
    #[test]
    fn unverified_streaming_shell_is_rejected_before_runtime_dispatch() {
        let active = ReviewActivity::default();
        let result = validate_terminal_message(
            &json!({"id": 10, "method": "command/exec", "params": {"processId": "smara-terminal-test"}}),
            &active,
            None,
        );
        let error = result.unwrap_err();
        assert!(error.contains("requires the verified Windows MXC backend") || error.contains("readiness has not been confirmed"));
        assert!(active.terminal_processes.is_empty());
        assert!(active.pending.is_empty());
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn verified_windows_terminal_still_requires_exact_project_and_network_policy() {
        let root = std::fs::canonicalize(std::env::current_dir().unwrap()).unwrap();
        let root = root.to_str().unwrap();
        let mut active = ReviewActivity::default();
        active.windows_sandbox_ready = true;
        let message = json!({
            "id": 10,
            "method": "command/exec",
            "params": {
                "processId": "smara-terminal-test",
                "command": ["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command",
                    "if (Get-Module PSReadLine) { Remove-Module PSReadLine -ErrorAction Stop }"],
                "cwd": root,
                "tty": true,
                "streamStdin": true,
                "streamStdoutStderr": true,
                "disableTimeout": true,
                "outputBytesCap": 8388608,
                "size": {"rows": 24, "cols": 80},
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "writableRoots": [root],
                    "networkAccess": false
                }
            }
        });
        assert!(validate_terminal_message(&message, &active, Some(std::path::Path::new(root))).is_ok());
        let mut injected_startup = message.clone();
        injected_startup["params"]["command"][5] = json!("Write-Output 'untrusted startup'");
        assert!(validate_terminal_message(&injected_startup, &active, Some(std::path::Path::new(root))).is_err());
        let mut network_enabled = message.clone();
        network_enabled["params"]["sandboxPolicy"]["networkAccess"] = json!(true);
        assert!(validate_terminal_message(&network_enabled, &active, Some(std::path::Path::new(root))).is_err());
        let mut extra_root = message.clone();
        extra_root["params"]["sandboxPolicy"]["writableRoots"] = json!([root, root]);
        assert!(validate_terminal_message(&extra_root, &active, Some(std::path::Path::new(root))).is_err());
        active.windows_sandbox_ready = false;
        assert!(validate_terminal_message(&message, &active, Some(std::path::Path::new(root))).is_err());
        assert!(active.terminal_processes.is_empty());
    }
}

#[cfg(test)]
mod transport_tests {
    use super::*;
    use std::time::{Duration, Instant};

    #[derive(Clone)]
    struct Sink { bytes: Arc<Mutex<Vec<u8>>>, delay: Duration }
    impl Write for Sink {
        fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
            if !self.delay.is_zero() { std::thread::sleep(self.delay); }
            self.bytes.lock().unwrap().extend_from_slice(buf);
            Ok(buf.len())
        }
        fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
    }

    #[test]
    fn writer_keeps_lines_in_order_and_enqueue_never_waits_for_the_pipe() {
        let bytes = Arc::new(Mutex::new(Vec::new()));
        // Each write takes 20 ms, like a busy pipe. Enqueueing 20 lines must still return at once.
        let tx = spawn_writer(Sink { bytes: bytes.clone(), delay: Duration::from_millis(20) });
        let started = Instant::now();
        for i in 0..20 { tx.send(format!("{{\"id\":{i}}}")).unwrap(); }
        assert!(started.elapsed() < Duration::from_millis(50), "enqueue waited for the pipe: {:?}", started.elapsed());
        drop(tx);
        let expected: String = (0..20).map(|i| format!("{{\"id\":{i}}}\n")).collect();
        let deadline = Instant::now() + Duration::from_secs(5);
        while bytes.lock().unwrap().len() < expected.len() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(String::from_utf8(bytes.lock().unwrap().clone()).unwrap(), expected);
    }

    #[test]
    fn only_model_project_and_option_changes_or_the_active_key_restart_the_runtime() {
        let running = Some(("local:glm", "GLM_KEY"));
        let req = |value: Value| value;
        assert!(needs_runtime_restart("select_model", &req(json!({"id": "local:other"})), running));
        assert!(needs_runtime_restart("select_project", &req(json!({})), running));
        assert!(needs_runtime_restart("save_project_options", &req(json!({})), running));
        assert!(needs_runtime_restart("save_model", &req(json!({"profile": {"id": "local:glm"}})), running));
        assert!(!needs_runtime_restart("save_model", &req(json!({"profile": {"id": "local:other"}})), running));
        assert!(needs_runtime_restart("save_credential", &req(json!({"name": "GLM_KEY"})), running));
        assert!(!needs_runtime_restart("save_credential", &req(json!({"name": "TAVILY_API_KEY"})), running));
        for op in ["save_memory", "select_search", "test_model", "test_search", "save_outreach", "add_project"] {
            assert!(!needs_runtime_restart(op, &req(json!({})), running), "{op} must not restart");
        }
        assert!(!needs_runtime_restart("select_model", &req(json!({})), None), "nothing to restart when idle");
    }
}
