//! Smara's native-source runtime transport. No model or tool loop lives here.
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Stdio};
use std::sync::{Mutex, OnceLock};
use tauri::{AppHandle, Emitter};

struct NativeSession {
    process: Child,
    input: Option<ChildStdin>,
    generation: String,
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
        Ok(path) => json!({"built": true, "binary": path, "sourceOwned": true}),
        Err(error) => json!({"built": false, "error": error, "sourceOwned": true}),
    }
}

#[tauri::command]
pub fn native_start(app: AppHandle, workspace: String, profile_id: String, tools_enabled: Option<bool>, workers_enabled: Option<bool>, browser_origins: Option<Vec<String>>) -> Result<Value, String> {
    let mut lock = session().lock().map_err(|_| "Native session lock failed")?;
    if let Some(running) = lock.as_mut() {
        if running.process.try_wait().map_err(|_| "Cannot inspect native process")?.is_none() {
            return Err("Stop the current native runtime before changing workspace or model.".into());
        }
        // Reap a crashed transport before reconnecting; do not strand the UI
        // behind an already-exited global session.
        *lock = None;
    }
    let native_binary = binary()?;
    let connection = super::current_connection();
    let root = super::selected_workspace_dir(&workspace, &connection.allowed_roots)?
        .ok_or("Choose an existing workspace folder")?;
    let mut matches = super::stored_local_model_profiles().into_iter()
        .filter(|profile| profile.id == profile_id).collect::<Vec<_>>();
    if matches.len() != 1 { return Err("Select one unambiguous configured model in Settings".into()); }
    let profile = matches.remove(0);
    let secret = super::resolve_local_secret(&profile.credential_name)?;
    let generation = format!("native-{}", chrono::Utc::now().timestamp_nanos_opt().unwrap_or_default());
    let mut command = super::executor_command(&["--native-app-server".into()]);
    command.stdin(Stdio::piped()).current_dir(&root);
    let mut process = command.spawn().map_err(|error| format!("Cannot start native transport: {error}"))?;
    let mut input = process.stdin.take().ok_or("Native transport stdin is missing")?;
    let config = json!({"binary": native_binary, "workspace": root,
        "home": super::app_data_dir().join("native-runtime"), "model": profile.model,
        "base_url": profile.base_url, "api_key": secret, "auth_header": profile.auth_header,
        "tools_enabled": tools_enabled.unwrap_or(false), "workers_enabled": workers_enabled.unwrap_or(false), "browser_origins": browser_origins.unwrap_or_default()});
    if writeln!(input, "{config}").and_then(|_| input.flush()).is_err() {
        let _ = process.kill(); let _ = process.wait();
        return Err("Cannot configure native transport".into());
    }
    let output = process.stdout.take().ok_or("Native transport stdout is missing")?;
    let errors = process.stderr.take().ok_or("Native transport stderr is missing")?;
    let event_generation = generation.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(output).lines() {
            let Ok(line) = line else { break; };
            match serde_json::from_str::<Value>(&line) {
                Ok(message) => { let _ = app.emit("native-runtime-event", json!({"generation": event_generation, "message": message})); }
                Err(_) => { let _ = app.emit("native-runtime-event", json!({"generation": event_generation, "message": {"method": "smara/runtimeError", "params": {"message": "Native runtime returned invalid protocol data"}}})); }
            }
        }
        let _ = app.emit("native-runtime-event", json!({"generation": event_generation, "message": {"method": "smara/runtimeStopped"}}));
    });
    std::thread::spawn(move || { for _line in BufReader::new(errors).lines() {} });
    *lock = Some(NativeSession { process, input: Some(input), generation: generation.clone() });
    Ok(json!({"generation": generation, "sourceOwned": true}))
}

#[tauri::command]
pub fn native_send(generation: String, message: Value) -> Result<(), String> {
    if !message.is_object() { return Err("RPC message must be an object".into()); }
    let mut lock = session().lock().map_err(|_| "Native session lock failed")?;
    let running = lock.as_mut().ok_or("Native runtime is not connected")?;
    if running.generation != generation { return Err("Stale native runtime generation".into()); }
    if running.process.try_wait().map_err(|_| "Cannot inspect native process")?.is_some() {
        return Err("Native process exited; reconnect before sending work".into());
    }
    let input = running.input.as_mut().ok_or("Native runtime is stopping")?;
    writeln!(input, "{message}").and_then(|_| input.flush()).map_err(|_| "Cannot send native RPC message".into())
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
        if let Some(mut input) = running.input.take() {
            let _ = writeln!(input, "{{\"method\":\"smara/shutdown\"}}");
            let _ = input.flush();
        }
        // Allow the native EOF cleanup (bounded to 45s by the bootstrap) to
        // finish before killing its transport process.
        for _ in 0..550 {
            if running.process.try_wait().map_err(|_| "Cannot inspect stopping process")?.is_some() {
                *lock = None;
                return Ok(());
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        running.process.kill().map_err(|_| "Could not stop native transport")?;
        running.process.wait().map_err(|_| "Could not reap native transport")?;
        *lock = None;
    }
    Ok(())
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
