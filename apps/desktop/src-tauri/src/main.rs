#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::io::Write;
use std::process::{Command, Stdio};
use tauri::{AppHandle, Emitter};

const DEFAULT_API_URL: &str = "https://ai.syntarus.com/smara-api";
const DEFAULT_WEB_URL: &str = "https://ai.syntarus.com";

#[derive(Debug, Clone, Serialize)]
struct ConnectionState {
    api_url: String,
    web_url: String,
    workspace: String,
    model_profile: String,
    paired: bool,
    executor_id: Option<String>,
    capabilities: Vec<String>,
    allowed_roots: Vec<String>,
    terminal_allowlist: Vec<String>,
    browser_domains: Vec<String>,
    paused: bool,
    running: bool,
    pid: Option<u32>,
    log_path: String,
    has_cli_token: bool,
    last_error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct RemoteStatus {
    ok: bool,
    api_url: String,
    detail: String,
}

#[derive(Debug, Deserialize)]
struct LocalSettings {
    api_url: String,
    web_url: String,
    workspace: String,
    model_profile: String,
    allowed_roots: Vec<String>,
    terminal_allowlist: Vec<String>,
    browser_domains: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct PairArgs {
    api_url: String,
    code: String,
    allowed_roots: Vec<String>,
    terminal_allowlist: Vec<String>,
    browser_domains: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct ChatArgs {
    api_url: String,
    workspace: String,
    model_profile: String,
    message: String,
    conversation_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct LocalCredentialSummary {
    name: String,
    provider: String,
    updated_at: String,
}

fn app_data_dir() -> PathBuf {
    if let Some(value) = std::env::var_os("APPDATA") {
        return PathBuf::from(value).join("Smara");
    }
    if let Some(value) = std::env::var_os("XDG_CONFIG_HOME") {
        return PathBuf::from(value).join("Smara");
    }
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".config")
        .join("Smara")
}

fn state_path() -> PathBuf {
    if let Some(value) = std::env::var_os("SMARA_DESKTOP_STATE") {
        return PathBuf::from(value);
    }
    app_data_dir().join("desktop.json")
}

fn preferences_path() -> PathBuf {
    app_data_dir().join("desktop-ui.json")
}

fn runtime_path() -> PathBuf {
    app_data_dir().join("desktop-ui.runtime.json")
}

fn pause_path() -> PathBuf {
    let mut path = state_path();
    let suffix = format!("{}.paused", path.extension().and_then(|e| e.to_str()).unwrap_or("json"));
    path.set_extension(suffix);
    path
}

fn log_path() -> PathBuf {
    let root = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(app_data_dir);
    root.join("Smara").join("logs").join("desktop.log")
}

fn cli_token_path() -> PathBuf {
    if let Some(value) = std::env::var_os("SMARA_TOKEN_FILE") { PathBuf::from(value) } else { app_data_dir().join("token.json") }
}

fn read_json(path: &Path) -> Option<Value> {
    fs::read_to_string(path).ok().and_then(|text| serde_json::from_str(&text).ok())
}

fn write_json(path: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("Could not create {}: {error}", parent.display()))?;
    }
    let serialized = serde_json::to_string_pretty(value).map_err(|error| error.to_string())?;
    fs::write(path, serialized).map_err(|error| format!("Could not write {}: {error}", path.display()))
}

fn string_list(value: Option<&Value>) -> Vec<String> {
    value.and_then(Value::as_array).map(|items| items.iter().filter_map(Value::as_str).map(str::to_owned).collect()).unwrap_or_default()
}

fn executor_executable() -> PathBuf {
    if let Some(value) = std::env::var_os("SMARA_DESKTOP_EXECUTABLE") {
        return PathBuf::from(value);
    }
    // Release installers carry a PyInstaller-built executor beside the
    // native app. Development still prefers the repository virtualenv below.
    if let Ok(current) = std::env::current_exe() {
        if let Some(parent) = current.parent() {
            let bundled = parent.join("resources").join(if cfg!(windows) { "smara-desktop.exe" } else { "smara-desktop" });
            if bundled.is_file() {
                return bundled;
            }
        }
    }
    if let Some(root) = std::env::var_os("SMARA_REPO_ROOT") {
        let root = PathBuf::from(root);
        #[cfg(windows)]
        return root.join(".venv").join("Scripts").join("smara-desktop.exe");
        #[cfg(not(windows))]
        return root.join(".venv").join("bin").join("smara-desktop");
    }
    #[cfg(windows)]
    { PathBuf::from("smara-desktop.exe") }
    #[cfg(not(windows))]
    { PathBuf::from("smara-desktop") }
}

fn command_hidden(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
}

fn executor_command(args: &[String]) -> Command {
    let mut command = Command::new(executor_executable());
    command.args(args).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    command.env("SMARA_DESKTOP_STATE", state_path());
    command_hidden(&mut command);
    command
}

fn process_alive(pid: u32) -> bool {
    #[cfg(windows)]
    {
        let mut command = Command::new("tasklist.exe");
        command.args(["/FI", &format!("PID eq {pid}"), "/NH"]);
        command_hidden(&mut command);
        return command.output().map(|output| String::from_utf8_lossy(&output.stdout).contains(&pid.to_string())).unwrap_or(false);
    }
    #[cfg(not(windows))]
    {
        let status = Command::new("kill").args(["-0", &pid.to_string()]).status();
        status.map(|value| value.success()).unwrap_or(false)
    }
}

fn cli_token() -> Result<String, String> {
    let path = cli_token_path();
    let data = read_json(&path).ok_or_else(|| "Sign in from Settings before using hosted chat.".to_owned())?;
    data.get("access_token").and_then(Value::as_str).filter(|value| !value.is_empty()).map(str::to_owned).ok_or_else(|| "Your Smara sign-in is missing. Open Settings and sign in again.".to_owned())
}

#[tauri::command]
async fn login_cli(api_url: String, web_url: String) -> Result<String, String> {
    let api_url = api_url.trim().trim_end_matches('/').to_owned();
    if !(api_url.starts_with("http://") || api_url.starts_with("https://")) {
        return Err("Smara API URL must start with http:// or https://".to_owned());
    }
    let client = reqwest::Client::builder().timeout(std::time::Duration::from_secs(15)).build().map_err(|error| error.to_string())?;
    let request = client.get(format!("{api_url}/v1/cli/device/request")).query(&[("name", "Smara Desktop")]).send().await.map_err(|error| format!("Could not start sign-in: {error}"))?;
    if !request.status().is_success() {
        return Err(format!("Smara sign-in could not start (HTTP {})", request.status()));
    }
    let device: Value = request.json().await.map_err(|error| format!("Smara sign-in returned invalid data: {error}"))?;
    let device_code = device.get("device_code").and_then(Value::as_str).filter(|value| !value.is_empty()).ok_or_else(|| "Smara sign-in did not return a device code.".to_owned())?;
    let mut auth_url = reqwest::Url::parse(web_url.trim().trim_end_matches('/')).map_err(|_| "Smara Web URL must be a valid HTTP(S) URL.".to_owned())?;
    if !matches!(auth_url.scheme(), "http" | "https") {
        return Err("Smara Web URL must start with http:// or https://".to_owned());
    }
    auth_url.query_pairs_mut().append_pair("cli_device", device_code);
    if !open::that(auth_url.as_str()).is_ok() {
        return Err(format!("Open this URL to finish sign-in: {auth_url}"));
    }
    let interval = device.get("interval").and_then(Value::as_u64).unwrap_or(2).clamp(1, 10);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(300);
    loop {
        if std::time::Instant::now() >= deadline {
            return Err("Sign-in timed out. Choose Sign in again to create a fresh request.".to_owned());
        }
        tokio::time::sleep(std::time::Duration::from_secs(interval)).await;
        let response = client.get(format!("{api_url}/v1/cli/device/poll")).query(&[("device_code", device_code)]).send().await.map_err(|error| format!("Sign-in polling failed: {error}"))?;
        if !response.status().is_success() {
            return Err(format!("Smara sign-in polling returned HTTP {}", response.status()));
        }
        let result: Value = response.json().await.map_err(|error| format!("Smara sign-in returned invalid data: {error}"))?;
        match result.get("status").and_then(Value::as_str) {
            Some("pending") => continue,
            Some("expired") | Some("used") => return Err("That sign-in request expired or was already used. Try again.".to_owned()),
            Some("approved") => {
                let token = result.get("access_token").and_then(Value::as_str).filter(|value| !value.is_empty()).ok_or_else(|| "Smara sign-in did not return a token.".to_owned())?;
                write_json(&app_data_dir().join("token.json"), &json!({"access_token": token, "token_type": "bearer", "expires_in": result.get("expires_in").cloned().unwrap_or(Value::Null)}))?;
                return Ok("Smara Desktop is signed in.".to_owned());
            }
            _ => return Err("Smara returned an unknown sign-in status.".to_owned()),
        }
    }
}

fn current_connection() -> ConnectionState {
    let state = read_json(&state_path());
    let preferences = read_json(&preferences_path());
    let api_url = preferences.as_ref().and_then(|value| value.get("api_url")).and_then(Value::as_str).or_else(|| state.as_ref().and_then(|value| value.get("smara_url")).and_then(Value::as_str)).unwrap_or(DEFAULT_API_URL).trim_end_matches('/').to_owned();
    let web_url = preferences.as_ref().and_then(|value| value.get("web_url")).and_then(Value::as_str).unwrap_or(DEFAULT_WEB_URL).trim_end_matches('/').to_owned();
    let workspace = preferences.as_ref().and_then(|value| value.get("workspace")).and_then(Value::as_str).unwrap_or("default").to_owned();
    let model_profile = preferences.as_ref().and_then(|value| value.get("model_profile")).and_then(Value::as_str).unwrap_or("default").to_owned();
    let pid = read_json(&runtime_path()).and_then(|value| value.get("pid").and_then(Value::as_u64).map(|value| value as u32)).filter(|value| process_alive(*value));
    let paired = state.as_ref().map(|value| value.get("executor_id").and_then(Value::as_str).is_some() && (value.get("token").and_then(Value::as_str).is_some() || value.get("token_dpapi").and_then(Value::as_str).is_some())).unwrap_or(false);
    let capabilities = string_list(state.as_ref().and_then(|value| value.get("capabilities")));
    let configured_list = |key: &str| -> Vec<String> {
        if let Some(value) = preferences.as_ref().and_then(|item| item.get(key)) { string_list(Some(value)) } else { string_list(state.as_ref().and_then(|item| item.get(key))) }
    };
    let allowed_roots = configured_list("allowed_roots");
    let terminal_allowlist = configured_list("terminal_allowlist");
    let browser_domains = configured_list("browser_domains");
    let token_path = cli_token_path();
    ConnectionState { api_url, web_url, workspace, model_profile, paired, executor_id: state.as_ref().and_then(|value| value.get("executor_id")).and_then(Value::as_str).map(str::to_owned), capabilities, allowed_roots, terminal_allowlist, browser_domains, paused: pause_path().exists(), running: pid.is_some(), pid, log_path: log_path().display().to_string(), has_cli_token: read_json(&token_path).map(|value| value.get("access_token").and_then(Value::as_str).map(|token| !token.is_empty()).unwrap_or(false)).unwrap_or(false), last_error: None }
}

#[tauri::command]
fn load_connection() -> ConnectionState { current_connection() }

#[tauri::command]
fn save_settings(settings: LocalSettings) -> Result<ConnectionState, String> {
    let api_url = settings.api_url.trim().trim_end_matches('/');
    if !(api_url.starts_with("http://") || api_url.starts_with("https://")) { return Err("Smara API URL must start with http:// or https://".to_owned()); }
    let web_url = settings.web_url.trim().trim_end_matches('/');
    if !(web_url.starts_with("http://") || web_url.starts_with("https://")) { return Err("Smara Web URL must start with http:// or https://".to_owned()); }
    let value = json!({ "api_url": api_url, "web_url": web_url, "workspace": if settings.workspace.trim().is_empty() { "default" } else { settings.workspace.trim() }, "model_profile": if settings.model_profile.trim().is_empty() { "default" } else { settings.model_profile.trim() }, "allowed_roots": settings.allowed_roots, "terminal_allowlist": settings.terminal_allowlist, "browser_domains": settings.browser_domains });
    write_json(&preferences_path(), &value)?;
    if let Some(mut state) = read_json(&state_path()) {
        if let Some(object) = state.as_object_mut() {
            object.insert("smara_url".to_owned(), Value::String(api_url.to_owned()));
            object.insert("allowed_roots".to_owned(), value["allowed_roots"].clone());
            object.insert("terminal_allowlist".to_owned(), value["terminal_allowlist"].clone());
            object.insert("browser_domains".to_owned(), value["browser_domains"].clone());
            write_json(&state_path(), &state)?;
        }
    }
    Ok(current_connection())
}

#[tauri::command]
async fn check_connection(api_url: String) -> Result<RemoteStatus, String> {
    let api_url = api_url.trim().trim_end_matches('/').to_owned();
    let response = reqwest::Client::new().get(format!("{api_url}/health")).timeout(std::time::Duration::from_secs(8)).send().await.map_err(|error| format!("Hosted Smara is unreachable: {error}"))?;
    let status = response.status();
    if !status.is_success() { return Ok(RemoteStatus { ok: false, api_url, detail: format!("Hosted Smara returned HTTP {status}") }); }
    Ok(RemoteStatus { ok: true, api_url, detail: "Hosted Smara is ready".to_owned() })
}

fn run_executor(args: Vec<String>) -> Result<String, String> {
    let output = executor_command(&args).output().map_err(|error| format!("Could not start the local executor: {error}. Set SMARA_DESKTOP_EXECUTABLE if Smara is not on PATH."))?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if !output.status.success() { return Err(if stderr.is_empty() { stdout } else { stderr }); }
    Ok(stdout)
}

fn run_executor_with_input(args: Vec<String>, input: &str) -> Result<String, String> {
    let mut command = executor_command(&args);
    command.stdin(Stdio::piped());
    let mut child = command.spawn().map_err(|error| format!("Could not start the local credential vault: {error}"))?;
    child.stdin.take().ok_or_else(|| "Could not open the local credential vault input.".to_owned())?.write_all(input.as_bytes()).map_err(|error| format!("Could not save the local credential: {error}"))?;
    let output = child.wait_with_output().map_err(|error| format!("Could not finish saving the local credential: {error}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if !output.status.success() { return Err(if stderr.is_empty() { stdout } else { stderr }); }
    Ok(stdout)
}

#[tauri::command]
fn list_local_credentials() -> Result<Vec<LocalCredentialSummary>, String> {
    let output = run_executor(vec!["--credential-list".to_owned()])?;
    serde_json::from_str(&output).map_err(|_| "The local credential vault returned invalid data.".to_owned())
}

#[tauri::command]
fn save_local_credential(name: String, provider: String, secret: String) -> Result<Vec<LocalCredentialSummary>, String> {
    if secret.is_empty() { return Err("Enter a credential value before saving.".to_owned()); }
    run_executor_with_input(vec!["--credential-set".to_owned(), name, "--credential-provider".to_owned(), provider], &secret)?;
    list_local_credentials()
}

#[tauri::command]
fn delete_local_credential(name: String) -> Result<Vec<LocalCredentialSummary>, String> {
    run_executor(vec!["--credential-delete".to_owned(), name])?;
    list_local_credentials()
}

#[tauri::command]
fn pair_desktop(args: PairArgs) -> Result<ConnectionState, String> {
    if args.code.trim().len() != 8 { return Err("Pairing code must be 8 characters.".to_owned()); }
    let mut command_args = vec!["--api".to_owned(), args.api_url.trim_end_matches('/').to_owned(), "--pair".to_owned(), args.code.trim().to_uppercase(), "--pair-only".to_owned(), "--state".to_owned(), state_path().display().to_string()];
    for root in args.allowed_roots { command_args.extend(["--allow-root".to_owned(), root]); }
    for executable in args.terminal_allowlist { command_args.extend(["--terminal-allow".to_owned(), executable]); }
    for domain in args.browser_domains { command_args.extend(["--browser-domain".to_owned(), domain]); }
    run_executor(command_args)?;
    Ok(current_connection())
}

#[tauri::command]
fn start_executor() -> Result<ConnectionState, String> {
    if !current_connection().paired { return Err("Pair this desktop before starting the executor.".to_owned()); }
    if current_connection().running { return Ok(current_connection()); }
    let command_args = vec!["--state".to_owned(), state_path().display().to_string()];
    let mut child = executor_command(&command_args).spawn().map_err(|error| format!("Could not start Smara Desktop: {error}. Set SMARA_DESKTOP_EXECUTABLE if needed."))?;
    let pid = child.id();
    // The executor is intentionally detached; the UI may close while approved work continues.
    let _ = child.stdout.take();
    let _ = child.stderr.take();
    write_json(&runtime_path(), &json!({"pid": pid}))?;
    Ok(current_connection())
}

#[tauri::command]
fn stop_executor() -> Result<ConnectionState, String> {
    let pid = read_json(&runtime_path()).and_then(|value| value.get("pid").and_then(Value::as_u64).map(|value| value as u32));
    if let Some(pid) = pid {
        if process_alive(pid) {
            #[cfg(windows)]
            {
                let mut command = Command::new("taskkill.exe"); command.args(["/PID", &pid.to_string(), "/T", "/F"]); command_hidden(&mut command); command.output().map_err(|error| format!("Could not stop executor: {error}"))?;
            }
            #[cfg(not(windows))]
            { Command::new("kill").args(["-TERM", &pid.to_string()]).status().map_err(|error| format!("Could not stop executor: {error}"))?; }
        }
    }
    let _ = fs::remove_file(runtime_path());
    Ok(current_connection())
}

#[tauri::command]
fn pause_executor() -> Result<ConnectionState, String> { run_executor(vec!["--state".to_owned(), state_path().display().to_string(), "--pause".to_owned()])?; Ok(current_connection()) }

#[tauri::command]
fn resume_executor() -> Result<ConnectionState, String> { run_executor(vec!["--state".to_owned(), state_path().display().to_string(), "--resume".to_owned()])?; Ok(current_connection()) }

#[tauri::command]
fn revoke_executor() -> Result<ConnectionState, String> {
    // Stop the tracked child before revoking its token so no old process can
    // continue polling during the short server-side revocation race window.
    let _ = stop_executor()?;
    run_executor(vec!["--state".to_owned(), state_path().display().to_string(), "--revoke".to_owned()])?;
    let _ = fs::remove_file(runtime_path());
    Ok(current_connection())
}

#[tauri::command]
fn read_log() -> Result<String, String> {
    let text = fs::read_to_string(log_path()).unwrap_or_else(|_| "No local executor log yet.".to_owned());
    let lines: Vec<&str> = text.lines().rev().take(80).collect();
    Ok(lines.into_iter().rev().collect::<Vec<_>>().join("\n"))
}

#[tauri::command]
async fn load_tasks() -> Result<Vec<Value>, String> {
    let token = cli_token()?;
    let api_url = current_connection().api_url;
    let response = reqwest::Client::new().get(format!("{api_url}/v1/tasks")).bearer_auth(token).timeout(std::time::Duration::from_secs(12)).send().await.map_err(|error| format!("Could not load hosted tasks: {error}"))?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED {
        let _ = fs::remove_file(cli_token_path());
        return Err("401: Your Smara sign-in expired. Sign in again to load tasks.".to_owned());
    }
    if !response.status().is_success() { return Err(format!("Hosted task list returned HTTP {}", response.status())); }
    let value: Value = response.json().await.map_err(|error| format!("Hosted task list was invalid: {error}"))?;
    Ok(value.as_array().cloned().or_else(|| value.get("tasks").and_then(Value::as_array).cloned()).unwrap_or_default())
}

#[tauri::command]
async fn stream_chat(app: AppHandle, args: ChatArgs) -> Result<(), String> {
    if args.message.trim().is_empty() { return Err("Message cannot be empty.".to_owned()); }
    let token = cli_token()?;
    let mut payload = json!({ "message": args.message, "workspace_id": if args.workspace.trim().is_empty() { "default" } else { args.workspace.trim() }, "conversation_id": args.conversation_id });
    if !args.model_profile.trim().is_empty() && args.model_profile.trim() != "default" { payload["model_profile"] = Value::String(args.model_profile.trim().to_owned()); }
    let client = reqwest::Client::builder().timeout(std::time::Duration::from_secs(3600)).build().map_err(|error| format!("Could not prepare chat: {error}"))?;
    let response = client.post(format!("{}/v1/chat/stream", args.api_url.trim_end_matches('/'))).bearer_auth(token).header("Accept", "text/event-stream").json(&payload).send().await.map_err(|error| format!("Could not start chat: {error}"))?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED {
        let _ = fs::remove_file(cli_token_path());
        return Err("Hosted chat returned HTTP 401; your Smara sign-in expired.".to_owned());
    }
    if !response.status().is_success() { return Err(format!("Hosted chat returned HTTP {}", response.status())); }
    let mut stream = response.bytes_stream();
    let mut buffer = String::new();
    let mut terminal_event = false;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|error| format!("Chat stream disconnected: {error}"))?;
        buffer.push_str(&String::from_utf8_lossy(&chunk));
        while let Some(index) = buffer.find('\n') {
            let line = buffer[..index].trim_end_matches('\r').to_owned();
            buffer.drain(..=index);
            if let Some(data) = line.strip_prefix("data:").map(str::trim_start) {
                if let Ok(value) = serde_json::from_str::<Value>(data) {
                    app.emit("smara-chat-event", value.clone()).map_err(|error| error.to_string())?;
                    if value.get("type").and_then(Value::as_str) == Some("done") || value.get("type").and_then(Value::as_str) == Some("error") { return Ok(()); }
                }
            }
        }
    }
    if !buffer.trim().is_empty() {
        if let Some(data) = buffer.trim().strip_prefix("data:").map(str::trim_start) { if let Ok(value) = serde_json::from_str::<Value>(data) { terminal_event = matches!(value.get("type").and_then(Value::as_str), Some("done") | Some("error")); app.emit("smara-chat-event", value).map_err(|error| error.to_string())?; } }
    }
    if !terminal_event {
        app.emit("smara-chat-event", json!({"type": "error", "message": "The hosted response ended before Smara finished. Retry this message."})).map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn open_web() -> Result<(), String> { open::that(current_connection().web_url).map_err(|error| format!("Could not open Smara Web: {error}")) }

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![load_connection, save_settings, check_connection, login_cli, pair_desktop, start_executor, stop_executor, pause_executor, resume_executor, revoke_executor, read_log, load_tasks, stream_chat, open_web, list_local_credentials, save_local_credential, delete_local_credential])
        .run(tauri::generate_context!())
        .expect("error while running Smara Desktop");
}
