//! Native-only Desktop shell. The Rust harness owns all agent/tool execution.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
mod native_runtime;
use native_runtime::{native_send, native_start, native_status, native_stop};

fn app_data_dir() -> PathBuf {
    std::env::var_os("SMARA_DESKTOP_DATA_DIR").map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("APPDATA").unwrap_or_default()).join("Smara"))
}
fn state_path() -> PathBuf {
    std::env::var_os("SMARA_DESKTOP_STATE").map(PathBuf::from).unwrap_or_else(|| app_data_dir().join("desktop.json"))
}
fn executor_executable() -> PathBuf {
    if let Some(path) = std::env::var_os("SMARA_DESKTOP_EXECUTABLE") { return PathBuf::from(path); }
    std::env::current_exe().unwrap_or_default().parent().unwrap_or(std::path::Path::new("."))
        .join("resources/smara-desktop.exe")
}
fn command_hidden(command: &mut Command) {
    #[cfg(windows)] {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
}
fn executor_command(args: &[String]) -> Command {
    let executable = executor_executable();
    let mut command = if executable.is_file() { Command::new(executable) } else {
        // Source fallback is development-only and targets the thin manager.
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let mut command = if cfg!(debug_assertions) {
            let mut command = Command::new(root.join(".venv/Scripts/python.exe"));
            command.args(["-m", "smara.native_management"]);
            command.env("PYTHONPATH", root.join("src"));
            command
        } else { Command::new(executable) };
        command_hidden(&mut command);
        command
    };
    command.args(args).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    command.env("SMARA_DESKTOP_STATE", state_path());
    command.env("PYTHONIOENCODING", "utf-8").env("PYTHONUTF8", "1");
    command_hidden(&mut command);
    command
}
fn management(request: Value) -> Result<Value, String> {
    let mut process = executor_command(&["--native-manage".into()]).spawn().map_err(|_| "Native settings companion is unavailable")?;
    let mut input = process.stdin.take().ok_or("Settings stdin unavailable")?;
    if write!(input, "{request}").is_err() {
        let _ = process.kill(); let _ = process.wait();
        return Err("Cannot send native settings request".into());
    }
    drop(input);
    let output = process.stdout.take().ok_or("Settings output unavailable")?;
    let reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        output.take(2_000_001).read_to_end(&mut bytes).map(|_| bytes)
    });
    let mut finished = false;
    for _ in 0..300 {
        if process.try_wait().map_err(|_| "Cannot inspect settings companion")?.is_some() { finished = true; break; }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    if !finished {
        let _ = process.kill(); let _ = process.wait();
        return Err("Settings request timed out; check saved state before retrying".into());
    }
    let bytes = reader.join().map_err(|_| "Cannot read settings response")?.map_err(|_| "Cannot read settings response")?;
    if bytes.len() > 2_000_000 { return Err("Settings response exceeds safety limit".into()); }
    let reply: Value = serde_json::from_slice(&bytes).map_err(|_| "Invalid settings response")?;
    if let Some(error) = reply.get("error").and_then(Value::as_str) { return Err(error.to_owned()); }
    reply.get("result").cloned().ok_or("Missing settings response".into())
}
#[tauri::command]
async fn native_manage(request: Value) -> Result<Value, String> {
    if !matches!(request.get("operation").and_then(Value::as_str), Some("bootstrap" | "add_project" | "select_project" | "select_model" | "select_search" | "save_credential" | "delete_credential" | "save_model")) {
        return Err("Unsupported UI settings operation; secret reads and legacy execution are unavailable".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        if request["operation"] == "bootstrap" { management(request) }
        else { native_runtime::with_disconnected(|| management(request)) }
    }).await.map_err(|_| "Settings worker failed".to_owned())?
}
struct Connection { allowed_roots: Vec<String> }
fn current_connection() -> Result<Connection, String> {
    let value = management(json!({"operation":"bootstrap"}))?;
    Ok(Connection { allowed_roots: value["preferences"]["projects"].as_array().ok_or("Projects are invalid")?
        .iter().filter_map(|p| p["workspace"].as_str().map(str::to_owned)).collect() })
}
fn selected_workspace_dir(workspace: &str, roots: &[String]) -> Result<Option<PathBuf>, String> {
    let path = PathBuf::from(workspace);
    if !path.is_absolute() { return Err("Choose an absolute project folder".into()); }
    let canonical = std::fs::canonicalize(path).map_err(|_| "Project folder is unavailable")?;
    if !canonical.is_dir() || !roots.iter().filter_map(|r| std::fs::canonicalize(r).ok()).any(|r| r == canonical) {
        return Err("Add this exact folder as a project first; another project's access does not apply".into());
    }
    Ok(Some(canonical))
}
#[derive(Deserialize)]
struct LocalModelProfile { id: String, model: String, base_url: String, credential_name: String, auth_header: String, context_window: Option<u64> }
fn stored_local_model_profiles() -> Result<Vec<LocalModelProfile>, String> {
    let value = management(json!({"operation":"bootstrap"}))?;
    serde_json::from_value(value["profiles"].clone()).map_err(|_| "Invalid native model profiles".into())
}
fn resolve_local_secret(name: &str) -> Result<String, String> {
    let value = management(json!({"operation":"resolve_credential","name":name}))?;
    let secret = value["secret"].as_str().unwrap_or_default();
    if secret.is_empty() { return Err("Model credential is missing or unreadable. Update it in Settings → Models".into()); }
    Ok(secret.to_owned())
}
#[tauri::command]
fn native_open_url(url: String) -> Result<(), String> {
    let parsed = reqwest::Url::parse(&url).map_err(|_| "Invalid link")?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() || !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("Only HTTP(S) links without credentials can be opened".into());
    }
    open::that_detached(parsed.as_str()).map_err(|_| "Cannot open link".into())
}
fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![native_status, native_start, native_send, native_stop, native_manage, native_open_url])
        .build(tauri::generate_context!()).expect("Could not build Smara Desktop")
        .run(|_, event| {
            if matches!(event, tauri::RunEvent::ExitRequested { .. }) { let _ = native_runtime::stop_current(); }
        });
}
