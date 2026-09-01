#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::io::Write;
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use tauri::{AppHandle, Emitter};

const DEFAULT_API_URL: &str = "https://ai.syntarus.com/smara-api";
const DEFAULT_WEB_URL: &str = "https://ai.syntarus.com/";

fn shared_http_client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(10))
            .timeout(std::time::Duration::from_secs(3600))
            .build()
            .expect("shared Smara HTTP client must build")
    })
}

#[derive(Debug, Clone, Serialize)]
struct ConnectionState {
    runtime_mode: String,
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
    auto_approve_safe: bool,
    approval_mode: String,
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
    #[serde(default)]
    runtime_mode: String,
    api_url: String,
    web_url: String,
    workspace: String,
    model_profile: String,
    allowed_roots: Vec<String>,
    terminal_allowlist: Vec<String>,
    browser_domains: Vec<String>,
    #[serde(default)]
    auto_approve_safe: bool,
    #[serde(default)]
    approval_mode: String,
}

#[derive(Debug, Deserialize)]
struct PairArgs {
    #[serde(default)]
    runtime_mode: String,
    api_url: String,
    code: String,
    allowed_roots: Vec<String>,
    terminal_allowlist: Vec<String>,
    browser_domains: Vec<String>,
    #[serde(default)]
    auto_approve_safe: bool,
    #[serde(default)]
    approval_mode: String,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
struct LocalConnectorSummary {
    provider: String,
    operation: String,
    credential_alias: String,
    auth_mode: String,
    risk: String,
    scopes: Vec<String>,
    max_results: u32,
    max_requests_per_run: u32,
    credential_configured: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct LocalModelProfile {
    id: String,
    label: String,
    provider: String,
    base_url: String,
    model: String,
    credential_name: String,
    auth_header: String,
    updated_at: String,
}

#[derive(Debug, Deserialize)]
struct LocalModelProfileInput {
    id: String,
    label: String,
    provider: String,
    base_url: String,
    model: String,
    api_key: String,
    auth_header: Option<String>,
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

fn model_credential_name(id: &str) -> String {
    let mut value = String::from("SMARA_MODEL_");
    for character in id.chars() {
        if character.is_ascii_alphanumeric() { value.push(character.to_ascii_uppercase()); }
        else if !value.ends_with('_') { value.push('_'); }
    }
    value.push_str("_API_KEY");
    value.chars().take(64).collect()
}

fn stored_local_model_profiles() -> Vec<LocalModelProfile> {
    let preferences = read_json(&preferences_path());
    let profiles = preferences
        .as_ref()
        .and_then(|value| value.get("local_model_profiles").cloned())
        .and_then(|value| serde_json::from_value::<Vec<LocalModelProfile>>(value).ok())
        .unwrap_or_default();
    if !profiles.is_empty() {
        return profiles;
    }

    // Older beta builds saved the selected profile and encrypted credential,
    // then lost the profile metadata when the general settings form was saved.
    // Recover the two built-in profiles when their local credential still
    // exists. Custom endpoints cannot be reconstructed safely and remain
    // intentionally opt-in through Settings.
    let Some(id) = preferences
        .as_ref()
        .and_then(|value| value.get("model_profile"))
        .and_then(Value::as_str)
        .and_then(|value| value.strip_prefix("local:"))
    else {
        return profiles;
    };
    let Some((label, provider, base_url, model, auth_header)) = (match id {
        "sarvam" => Some(("Sarvam", "sarvam", "https://api.sarvam.ai/v1/chat/completions", "sarvam-105b", "api-subscription-key")),
        "grok" => Some(("Grok", "grok", "https://api.x.ai/v1/chat/completions", "grok-3-mini", "authorization")),
        _ => None,
    }) else {
        return profiles;
    };
    let credential_name = model_credential_name(id);
    let credential_exists = read_json(&app_data_dir().join("credentials.json"))
        .and_then(|value| value.get(&credential_name).cloned())
        .is_some();
    if !credential_exists {
        return profiles;
    }
    let recovered = vec![LocalModelProfile {
        id: id.to_owned(),
        label: label.to_owned(),
        provider: provider.to_owned(),
        base_url: base_url.to_owned(),
        model: model.to_owned(),
        credential_name,
        auth_header: auth_header.to_owned(),
        updated_at: "legacy-recovered".to_owned(),
    }];
    // Persist the repaired metadata so a later ordinary settings save cannot
    // lose it again. If the preferences file is temporarily read-only, keep
    // the in-memory recovery usable and let the next save retry the migration.
    let _ = write_local_model_profiles(&recovered);
    recovered
}

fn write_local_model_profiles(profiles: &[LocalModelProfile]) -> Result<(), String> {
    let mut preferences = read_json(&preferences_path()).unwrap_or_else(|| json!({}));
    let object = preferences.as_object_mut().ok_or_else(|| "Desktop preferences are invalid.".to_owned())?;
    object.insert("local_model_profiles".to_owned(), serde_json::to_value(profiles).map_err(|error| error.to_string())?);
    write_json(&preferences_path(), &preferences)
}

fn preserve_local_model_profiles(mut value: Value, existing_profiles: Option<Value>) -> Value {
    if let Some(profiles) = existing_profiles {
        value["local_model_profiles"] = profiles;
    }
    value
}

fn string_list(value: Option<&Value>) -> Vec<String> {
    value.and_then(Value::as_array).map(|items| items.iter().filter_map(Value::as_str).map(str::to_owned).collect()).unwrap_or_default()
}

fn derived_local_capabilities(allowed_roots: &[String], terminal_allowlist: &[String], browser_domains: &[String]) -> Vec<String> {
    let mut capabilities = Vec::new();
    if allowed_roots.iter().any(|item| !item.trim().is_empty()) {
        capabilities.push("local_file_read".to_owned());
        capabilities.push("local_file_write".to_owned());
    }
    if terminal_allowlist.iter().any(|item| !item.trim().is_empty()) {
        capabilities.push("local_terminal".to_owned());
    }
    if browser_domains.iter().any(|item| !item.trim().is_empty()) {
        capabilities.push("local_browser".to_owned());
    }
    let connector_ready = read_json(&app_data_dir().join("credentials.json"))
        .and_then(|value| value.as_object().map(|object| object.contains_key("TAVILY_API_KEY") || object.contains_key("GITHUB_TOKEN")))
        .unwrap_or(false);
    if connector_ready {
        capabilities.push("local_integration".to_owned());
    }
    capabilities
}

fn sync_local_capabilities() -> Result<(), String> {
    let preferences = read_json(&preferences_path()).unwrap_or_else(|| json!({}));
    let allowed_roots = string_list(preferences.get("allowed_roots"));
    let terminal_allowlist = string_list(preferences.get("terminal_allowlist"));
    let browser_domains = string_list(preferences.get("browser_domains"));
    let capabilities = derived_local_capabilities(&allowed_roots, &terminal_allowlist, &browser_domains);
    let mut state = read_json(&state_path()).unwrap_or_else(|| json!({}));
    if let Some(object) = state.as_object_mut() {
        object.insert("runtime_mode".to_owned(), Value::String(
            preferences.get("runtime_mode").and_then(Value::as_str).unwrap_or("local").to_owned(),
        ));
        object.insert("allowed_roots".to_owned(), serde_json::to_value(allowed_roots).map_err(|error| error.to_string())?);
        object.insert("terminal_allowlist".to_owned(), serde_json::to_value(terminal_allowlist).map_err(|error| error.to_string())?);
        object.insert("browser_domains".to_owned(), serde_json::to_value(browser_domains).map_err(|error| error.to_string())?);
        object.insert("local_capabilities".to_owned(), serde_json::to_value(capabilities).map_err(|error| error.to_string())?);
    }
    write_json(&state_path(), &state)
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

/// Normalize the public Smara API URL without changing user-owned endpoints.
/// Older beta builds sometimes saved the public web root as the API URL. That
/// made `/health` look reachable while every authenticated route 404ed. The
/// hosted public root is known, so repair only that exact origin/path shape.
fn normalized_api_url(configured: &str) -> String {
    let configured = configured.trim();
    let Ok(mut url) = reqwest::Url::parse(if configured.is_empty() { DEFAULT_API_URL } else { configured }) else {
        return configured.to_owned();
    };
    if !matches!(url.scheme(), "http" | "https") {
        return configured.to_owned();
    }
    let is_public_smara = url.host_str() == Some("ai.syntarus.com");
    let path = url.path().trim_end_matches('/');
    if is_public_smara && (path.is_empty() || path == "/smara") {
        url.set_path("/smara-api");
        url.set_query(None);
        url.set_fragment(None);
    }
    url.to_string().trim_end_matches('/').to_owned()
}

/// Keep pairing tolerant of copied whitespace while rejecting anything that
/// is not part of the eight-character hex code issued by Smara Web.
fn normalized_pairing_code(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_ascii_hexdigit())
        .take(8)
        .map(|character| character.to_ascii_uppercase())
        .collect()
}

/// The Smara shell owns the hosted domain root. Older beta installs saved the
/// compatibility `/smara/` mount; repair that one known path while keeping
/// custom URLs intact.
fn normalized_web_url(api_url: &str, configured: &str) -> String {
    let configured = configured.trim();
    let fallback = DEFAULT_WEB_URL;
    let Ok(api) = reqwest::Url::parse(api_url) else { return configured.to_owned(); };
    let Ok(web) = reqwest::Url::parse(if configured.is_empty() { fallback } else { configured }) else { return configured.to_owned(); };
    let api_path = api.path().trim_end_matches('/');
    let same_host = api.scheme() == web.scheme() && api.host_str() == web.host_str() && api.port_or_known_default() == web.port_or_known_default();
    if api_path == "/smara-api" && same_host && web.path().trim_end_matches('/') == "/smara" {
        let mut repaired = web;
        repaired.set_path("/");
        repaired.set_query(None);
        repaired.set_fragment(None);
        return repaired.to_string();
    }
    configured.to_owned()
}

#[tauri::command]
async fn login_cli(api_url: String, web_url: String) -> Result<String, String> {
    let api_url = normalized_api_url(&api_url);
    if !(api_url.starts_with("http://") || api_url.starts_with("https://")) {
        return Err("Smara API URL must start with http:// or https://".to_owned());
    }
    let client = shared_http_client();
    let request = client.get(format!("{api_url}/v1/cli/device/request")).query(&[("name", "Smara Desktop")]).timeout(std::time::Duration::from_secs(15)).send().await.map_err(|error| format!("Could not start sign-in: {error}"))?;
    if !request.status().is_success() {
        let status = request.status();
        let detail = request.json::<Value>().await.ok().and_then(|value| value.get("detail").and_then(Value::as_str).map(str::to_owned));
        return Err(detail.unwrap_or_else(|| format!("Smara sign-in could not start (HTTP {status})")));
    }
    let device: Value = request.json().await.map_err(|error| format!("Smara sign-in returned invalid data: {error}"))?;
    let device_code = device.get("device_code").and_then(Value::as_str).filter(|value| !value.is_empty()).ok_or_else(|| "Smara sign-in did not return a device code.".to_owned())?;
    let web_url = normalized_web_url(&api_url, &web_url);
    let mut auth_url = reqwest::Url::parse(web_url.trim()).map_err(|_| "Smara Web URL must be a valid HTTP(S) URL.".to_owned())?;
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
        let response = client.get(format!("{api_url}/v1/cli/device/poll")).query(&[("device_code", device_code)]).timeout(std::time::Duration::from_secs(15)).send().await.map_err(|error| format!("Sign-in polling failed: {error}"))?;
        if !response.status().is_success() {
            let status = response.status();
            let detail = response.json::<Value>().await.ok().and_then(|value| value.get("detail").and_then(Value::as_str).map(str::to_owned));
            return Err(detail.unwrap_or_else(|| format!("Smara sign-in polling returned HTTP {status}")));
        }
        let result: Value = response.json().await.map_err(|error| format!("Smara sign-in returned invalid data: {error}"))?;
        match result.get("status").and_then(Value::as_str) {
            Some("pending") => continue,
            Some("expired") | Some("used") => return Err("That sign-in request expired or was already used. Try again.".to_owned()),
            Some("approved") => {
                let token = result.get("access_token").and_then(Value::as_str).filter(|value| !value.is_empty()).ok_or_else(|| "Smara sign-in did not return a token.".to_owned())?;
                // Write through the same resolver used by cli_token(), so an
                // explicit SMARA_TOKEN_FILE can never leave the UI signed in
                // to a file the chat path does not read.
                write_json(&cli_token_path(), &json!({"access_token": token, "token_type": "bearer", "expires_in": result.get("expires_in").cloned().unwrap_or(Value::Null)}))?;
                return Ok("Smara Desktop is signed in.".to_owned());
            }
            _ => return Err("Smara returned an unknown sign-in status.".to_owned()),
        }
    }
}

fn current_connection() -> ConnectionState {
    let state = read_json(&state_path());
    let preferences = read_json(&preferences_path());
    let configured_api_url = preferences.as_ref().and_then(|value| value.get("api_url")).and_then(Value::as_str).or_else(|| state.as_ref().and_then(|value| value.get("smara_url")).and_then(Value::as_str)).unwrap_or(DEFAULT_API_URL);
    let api_url = normalized_api_url(configured_api_url);
    let configured_web_url = preferences.as_ref().and_then(|value| value.get("web_url")).and_then(Value::as_str).unwrap_or(DEFAULT_WEB_URL);
    let web_url = normalized_web_url(&api_url, configured_web_url);
    let workspace = preferences.as_ref().and_then(|value| value.get("workspace")).and_then(Value::as_str).unwrap_or("default").to_owned();
    let model_profile = preferences.as_ref().and_then(|value| value.get("model_profile")).and_then(Value::as_str).unwrap_or("default").to_owned();
    let pid = read_json(&runtime_path()).and_then(|value| value.get("pid").and_then(Value::as_u64).map(|value| value as u32)).filter(|value| process_alive(*value));
    let paired = state.as_ref().map(|value| value.get("executor_id").and_then(Value::as_str).is_some() && (value.get("token").and_then(Value::as_str).is_some() || value.get("token_dpapi").and_then(Value::as_str).is_some())).unwrap_or(false);
    let paired_capabilities = string_list(state.as_ref().and_then(|value| value.get("capabilities")));
    let configured_list = |key: &str| -> Vec<String> {
        if let Some(value) = preferences.as_ref().and_then(|item| item.get(key)) { string_list(Some(value)) } else { string_list(state.as_ref().and_then(|item| item.get(key))) }
    };
    let allowed_roots = configured_list("allowed_roots");
    let terminal_allowlist = configured_list("terminal_allowlist");
    let browser_domains = configured_list("browser_domains");
    let auto_approve_safe = preferences.as_ref().and_then(|value| value.get("auto_approve_safe")).and_then(Value::as_bool)
        .or_else(|| state.as_ref().and_then(|value| value.get("auto_approve_safe")).and_then(Value::as_bool))
        .unwrap_or(false);
    let approval_mode = preferences.as_ref().and_then(|value| value.get("approval_mode")).and_then(Value::as_str)
        .or_else(|| state.as_ref().and_then(|value| value.get("approval_mode")).and_then(Value::as_str))
        .filter(|value| matches!(*value, "ask" | "auto"))
        .unwrap_or("ask").to_owned();
    // New installations are local-first.  Existing paired installations
    // without an explicit mode stay on the legacy cloud path until the user
    // chooses Local mode in Settings, preventing a surprise behavior change.
    let runtime_mode = preferences.as_ref().and_then(|value| value.get("runtime_mode")).and_then(Value::as_str)
        .or_else(|| state.as_ref().and_then(|value| value.get("runtime_mode")).and_then(Value::as_str))
        .filter(|value| matches!(*value, "local" | "cloud"))
        .unwrap_or(if state.is_some() { "cloud" } else { "local" }).to_owned();
    let capabilities = if runtime_mode == "local" {
        derived_local_capabilities(&allowed_roots, &terminal_allowlist, &browser_domains)
    } else {
        paired_capabilities
    };
    let token_path = cli_token_path();
    ConnectionState { runtime_mode, api_url, web_url, workspace, model_profile, paired, executor_id: state.as_ref().and_then(|value| value.get("executor_id")).and_then(Value::as_str).map(str::to_owned), capabilities, allowed_roots, terminal_allowlist, browser_domains, auto_approve_safe, approval_mode, paused: pause_path().exists(), running: pid.is_some(), pid, log_path: log_path().display().to_string(), has_cli_token: read_json(&token_path).and_then(|value| value.get("access_token").and_then(Value::as_str).map(|token| !token.is_empty())).unwrap_or(false), last_error: None }
}

#[tauri::command]
fn load_connection() -> ConnectionState { current_connection() }

#[tauri::command]
fn save_settings(settings: LocalSettings) -> Result<ConnectionState, String> {
    let api_url = normalized_api_url(&settings.api_url);
    if !(api_url.starts_with("http://") || api_url.starts_with("https://")) { return Err("Smara API URL must start with http:// or https://".to_owned()); }
    let web_url = normalized_web_url(&api_url, &settings.web_url);
    if !(web_url.starts_with("http://") || web_url.starts_with("https://")) { return Err("Smara Web URL must start with http:// or https://".to_owned()); }
    // Keep provider profile metadata when the general settings form is saved.
    // These profiles point at encrypted local credentials and must never be
    // dropped by an unrelated connection/permissions update.
    let existing_profiles = read_json(&preferences_path()).and_then(|value| value.get("local_model_profiles").cloned());
    let approval_mode = if settings.approval_mode.trim() == "auto" { "auto" } else { "ask" };
    let runtime_mode = if settings.runtime_mode.trim() == "cloud" { "cloud" } else { "local" };
    let local_capabilities = derived_local_capabilities(&settings.allowed_roots, &settings.terminal_allowlist, &settings.browser_domains);
    let mut value = json!({ "runtime_mode": runtime_mode, "api_url": api_url, "web_url": web_url, "workspace": if settings.workspace.trim().is_empty() { "default" } else { settings.workspace.trim() }, "model_profile": if settings.model_profile.trim().is_empty() { "default" } else { settings.model_profile.trim() }, "allowed_roots": settings.allowed_roots, "terminal_allowlist": settings.terminal_allowlist, "browser_domains": settings.browser_domains, "local_capabilities": local_capabilities, "auto_approve_safe": settings.auto_approve_safe, "approval_mode": approval_mode });
    value = preserve_local_model_profiles(value, existing_profiles);
    write_json(&preferences_path(), &value)?;
    let mut state = read_json(&state_path()).unwrap_or_else(|| json!({}));
    if let Some(object) = state.as_object_mut() {
        object.insert("smara_url".to_owned(), Value::String(api_url.to_owned()));
        object.insert("allowed_roots".to_owned(), value["allowed_roots"].clone());
        object.insert("terminal_allowlist".to_owned(), value["terminal_allowlist"].clone());
        object.insert("browser_domains".to_owned(), value["browser_domains"].clone());
        object.insert("local_capabilities".to_owned(), value["local_capabilities"].clone());
        object.insert("auto_approve_safe".to_owned(), value["auto_approve_safe"].clone());
        object.insert("approval_mode".to_owned(), value["approval_mode"].clone());
        object.insert("runtime_mode".to_owned(), value["runtime_mode"].clone());
        write_json(&state_path(), &state)?;
    }
    Ok(current_connection())
}

#[tauri::command]
async fn check_connection(api_url: String) -> Result<RemoteStatus, String> {
    let api_url = normalized_api_url(&api_url);
    let response = shared_http_client().get(format!("{api_url}/health")).timeout(std::time::Duration::from_secs(8)).send().await.map_err(|error| format!("Hosted Smara is unreachable: {error}"))?;
    let status = response.status();
    if !status.is_success() { return Ok(RemoteStatus { ok: false, api_url, detail: format!("Hosted Smara returned HTTP {status}") }); }
    let payload = response.json::<Value>().await.map_err(|_| RemoteStatus { ok: false, api_url: api_url.clone(), detail: "This URL returned a web page, not the Smara API. Use an API URL ending in /smara-api.".to_owned() });
    let payload = match payload {
        Ok(value) => value,
        Err(status) => return Ok(status),
    };
    if payload.get("ok").and_then(Value::as_bool) != Some(true) {
        return Ok(RemoteStatus { ok: false, api_url, detail: "Smara API health check did not return ok=true.".to_owned() });
    }
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
    let mut child = command.spawn().map_err(|error| format!("Could not start the local executor input: {error}"))?;
    child.stdin.take().ok_or_else(|| "Could not open the local executor input.".to_owned())?.write_all(input.as_bytes()).map_err(|error| format!("Could not send local executor input: {error}"))?;
    let output = child.wait_with_output().map_err(|error| format!("Could not finish the local executor request: {error}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if !output.status.success() { return Err(if stderr.is_empty() { stdout } else { stderr }); }
    Ok(stdout)
}

fn start_local_runner() -> Result<(), String> {
    let args = vec!["--state".to_owned(), state_path().display().to_string(), "--local-run".to_owned()];
    let mut command = executor_command(&args);
    command.stdout(Stdio::null()).stderr(Stdio::null());
    command.spawn().map_err(|error| format!("Could not start the private local runner: {error}"))?;
    Ok(())
}

fn create_private_local_task(request: &Value, conversation_id: &str) -> Result<Value, String> {
    let connection = current_connection();
    let capability = request.get("capability").and_then(Value::as_str).unwrap_or("");
    if !connection.capabilities.iter().any(|item| item == capability) {
        return Err(format!("{capability} is not enabled in Desktop Permissions."));
    }
    let title = request.get("title").and_then(Value::as_str).unwrap_or("").trim();
    let objective = request.get("objective").and_then(Value::as_str).unwrap_or("").trim();
    let executor_payload = request.get("payload").and_then(Value::as_object).ok_or_else(|| "The private model returned an invalid local action payload.".to_owned())?;
    if title.is_empty() || title.len() > 160 || objective.is_empty() || objective.len() > 8_000 {
        return Err("The private model returned an invalid local task title or objective.".to_owned());
    }
    let body = json!({
        "title": title,
        "objective": objective,
        "session_id": conversation_id,
        "requires_approval": true,
        "approval_mode": connection.approval_mode,
        "required_capability": capability,
        "payload": executor_payload,
    });
    let output = run_executor_with_input(
        vec!["--state".to_owned(), state_path().display().to_string(), "--local-task-create".to_owned()],
        &serde_json::to_string(&body).map_err(|error| error.to_string())?,
    )?;
    let task: Value = serde_json::from_str(&output).map_err(|_| "The private local task store returned invalid data.".to_owned())?;
    if task.get("status").and_then(Value::as_str) == Some("queued") {
        start_local_runner()?;
    }
    Ok(task)
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
    sync_local_capabilities()?;
    list_local_credentials()
}

#[tauri::command]
fn delete_local_credential(name: String) -> Result<Vec<LocalCredentialSummary>, String> {
    run_executor(vec!["--credential-delete".to_owned(), name])?;
    sync_local_capabilities()?;
    list_local_credentials()
}

#[tauri::command]
fn list_local_connectors() -> Result<Vec<LocalConnectorSummary>, String> {
    let output = run_executor(vec!["--connector-list".to_owned()])?;
    serde_json::from_str(&output).map_err(|_| "The local connector service returned invalid data.".to_owned())
}

#[tauri::command]
fn revoke_local_connector(provider: String) -> Result<Vec<LocalConnectorSummary>, String> {
    run_executor(vec!["--connector-revoke".to_owned(), provider])?;
    sync_local_capabilities()?;
    list_local_connectors()
}

#[tauri::command]
fn list_local_model_profiles() -> Vec<LocalModelProfile> { stored_local_model_profiles() }

#[tauri::command]
fn save_local_model_profile(profile: LocalModelProfileInput) -> Result<Vec<LocalModelProfile>, String> {
    let id = profile.id.trim().to_ascii_lowercase();
    if id.is_empty() || id.len() > 48 || !id.chars().all(|character| character.is_ascii_alphanumeric() || character == '-' || character == '_') {
        return Err("Use a short provider id with letters, numbers, hyphens, or underscores.".to_owned());
    }
    let label = profile.label.trim();
    if label.is_empty() || label.len() > 80 { return Err("Enter a provider name (up to 80 characters).".to_owned()); }
    let provider = profile.provider.trim().to_ascii_lowercase();
    if provider.is_empty() || provider.len() > 40 { return Err("Enter a provider name (up to 40 characters).".to_owned()); }
    let base_url = profile.base_url.trim().trim_end_matches('/');
    let parsed = reqwest::Url::parse(base_url).map_err(|_| "Endpoint must be a valid http(s) URL.".to_owned())?;
    if !matches!(parsed.scheme(), "http" | "https") { return Err("Endpoint must start with http:// or https://".to_owned()); }
    let model = profile.model.trim();
    if model.is_empty() || model.len() > 160 { return Err("Enter a model name (up to 160 characters).".to_owned()); }
    if profile.api_key.trim().is_empty() || profile.api_key.len() > 16_384 { return Err("Enter an API key before saving.".to_owned()); }
    let auth_header = profile.auth_header.as_deref().unwrap_or("authorization").trim().to_ascii_lowercase();
    if auth_header != "authorization" && auth_header != "api-subscription-key" { return Err("Choose Bearer authorization or api-subscription-key.".to_owned()); }
    let credential_name = model_credential_name(&id);
    let credential_provider = format!("model:{provider}");
    run_executor_with_input(vec!["--credential-set".to_owned(), credential_name.clone(), "--credential-provider".to_owned(), credential_provider], profile.api_key.trim())?;
    let mut profiles = stored_local_model_profiles();
    profiles.retain(|item| item.id != id);
    profiles.push(LocalModelProfile { id, label: label.to_owned(), provider, base_url: base_url.to_owned(), model: model.to_owned(), credential_name, auth_header, updated_at: chrono_like_now() });
    profiles.sort_by(|left, right| left.label.to_lowercase().cmp(&right.label.to_lowercase()));
    write_local_model_profiles(&profiles)?;
    Ok(profiles)
}

#[tauri::command]
fn delete_local_model_profile(id: String) -> Result<Vec<LocalModelProfile>, String> {
    let normalized = id.trim().to_ascii_lowercase();
    let mut profiles = stored_local_model_profiles();
    if let Some(profile) = profiles.iter().find(|item| item.id == normalized) {
        let _ = run_executor(vec!["--credential-delete".to_owned(), profile.credential_name.clone()]);
    }
    profiles.retain(|item| item.id != normalized);
    write_local_model_profiles(&profiles)?;
    Ok(profiles)
}

fn chrono_like_now() -> String {
    // Keep the native companion dependency-light; RFC3339 precision is not
    // needed for the UI and a monotonic-enough millisecond value is stable.
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now().duration_since(UNIX_EPOCH).map(|value| value.as_millis().to_string()).unwrap_or_else(|_| "0".to_owned())
}

fn resolve_local_secret(name: &str) -> Result<String, String> {
    let value = run_executor(vec!["--credential-get".to_owned(), name.to_owned()])?;
    if value.is_empty() { return Err("The local model credential is empty; save it again.".to_owned()); }
    Ok(value)
}

#[tauri::command]
fn pair_desktop(args: PairArgs) -> Result<ConnectionState, String> {
    let code = normalized_pairing_code(&args.code);
    if code.len() != 8 { return Err(format!("Pairing code must contain 8 hexadecimal characters ({}/8 entered).", code.len())); }
    let local_capabilities = derived_local_capabilities(&args.allowed_roots, &args.terminal_allowlist, &args.browser_domains);
    let mut command_args = vec!["--api".to_owned(), normalized_api_url(&args.api_url), "--pair".to_owned(), code, "--pair-only".to_owned(), "--state".to_owned(), state_path().display().to_string()];
    for root in args.allowed_roots { command_args.extend(["--allow-root".to_owned(), root]); }
    for executable in args.terminal_allowlist { command_args.extend(["--terminal-allow".to_owned(), executable]); }
    for domain in args.browser_domains { command_args.extend(["--browser-domain".to_owned(), domain]); }
    run_executor(command_args)?;
    let path = state_path();
    let mut state = read_json(&path).ok_or_else(|| "Desktop pairing state could not be read after pairing.".to_owned())?;
    state["auto_approve_safe"] = Value::Bool(args.auto_approve_safe);
    state["approval_mode"] = Value::String(if args.approval_mode.trim() == "auto" { "auto".to_owned() } else { "ask".to_owned() });
    state["runtime_mode"] = Value::String(if args.runtime_mode.trim() == "cloud" { "cloud".to_owned() } else { "local".to_owned() });
    state["local_capabilities"] = serde_json::to_value(local_capabilities).map_err(|error| error.to_string())?;
    write_json(&path, &state)?;
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
    if current_connection().runtime_mode == "local" {
        let output = run_executor(vec!["--state".to_owned(), state_path().display().to_string(), "--local-task-list".to_owned()])?;
        return serde_json::from_str(&output).map_err(|_| "The private local task store returned invalid data.".to_owned());
    }
    let token = cli_token()?;
    let api_url = current_connection().api_url;
    let response = shared_http_client().get(format!("{api_url}/v1/tasks")).bearer_auth(token).timeout(std::time::Duration::from_secs(12)).send().await.map_err(|error| format!("Could not load hosted tasks: {error}"))?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED {
        let _ = fs::remove_file(cli_token_path());
        return Err("401: Your Smara sign-in expired. Sign in again to load tasks.".to_owned());
    }
    if !response.status().is_success() { return Err(format!("Hosted task list returned HTTP {}", response.status())); }
    let value: Value = response.json().await.map_err(|error| format!("Hosted task list was invalid: {error}"))?;
    Ok(value.as_array().cloned().or_else(|| value.get("tasks").and_then(Value::as_array).cloned()).unwrap_or_default())
}

#[tauri::command]
async fn load_task_details(task_id: String) -> Result<Value, String> {
    if task_id.trim().is_empty() || task_id.len() > 160 || !task_id.chars().all(|character| character.is_ascii_alphanumeric() || matches!(character, '_' | '-')) {
        return Err("Task id is invalid.".to_owned());
    }
    if current_connection().runtime_mode == "local" || task_id.starts_with("local_") {
        let output = run_executor(vec!["--state".to_owned(), state_path().display().to_string(), "--local-task-detail".to_owned(), task_id])?;
        let detail: Value = serde_json::from_str(&output).map_err(|_| "The private local task store returned invalid data.".to_owned())?;
        let object = detail.as_object().ok_or_else(|| "The private local task record was invalid.".to_owned())?;
        let task = json!({
            "id": object.get("id").cloned().unwrap_or(Value::Null),
            "session_id": object.get("session_id").cloned().unwrap_or(Value::Null),
            "title": object.get("title").cloned().unwrap_or(Value::Null),
            "objective": object.get("objective").cloned().unwrap_or(Value::Null),
            "status": object.get("status").cloned().unwrap_or(Value::Null),
            "approval_mode": "desktop",
            "created_at": object.get("created_at").cloned().unwrap_or(Value::Null),
            "updated_at": object.get("updated_at").cloned().unwrap_or(Value::Null),
            "result": object.get("result").cloned().unwrap_or(Value::Null),
            "error": object.get("error").cloned().unwrap_or(Value::Null),
        });
        let mut wrapped = object.clone();
        wrapped.insert("task".to_owned(), task);
        return Ok(Value::Object(wrapped));
    }
    let token = cli_token()?;
    let api_url = current_connection().api_url;
    let client = shared_http_client();
    let mut values = serde_json::Map::new();
    for (key, suffix) in [("task", ""), ("steps", "/steps"), ("events", "/events"), ("artifacts", "/artifacts")] {
        let response = client.get(format!("{api_url}/v1/tasks/{task_id}{suffix}")).bearer_auth(&token).timeout(std::time::Duration::from_secs(12)).send().await.map_err(|error| format!("Could not load task details: {error}"))?;
        if response.status() == reqwest::StatusCode::UNAUTHORIZED {
            let _ = fs::remove_file(cli_token_path());
            return Err("401: Your Smara sign-in expired. Sign in again to load task details.".to_owned());
        }
        if !response.status().is_success() { return Err(format!("Task details returned HTTP {}", response.status())); }
        let value: Value = response.json().await.map_err(|error| format!("Task details were invalid: {error}"))?;
        values.insert(key.to_owned(), value);
    }
    Ok(Value::Object(values))
}

#[tauri::command]
fn decide_local_task(task_id: String, approved: bool) -> Result<(), String> {
    if task_id.trim().is_empty() || task_id.len() > 160 || !task_id.chars().all(|character| character.is_ascii_alphanumeric() || matches!(character, '_' | '-')) {
        return Err("Task id is invalid.".to_owned());
    }
    if current_connection().runtime_mode == "local" || task_id.starts_with("local_") {
        if approved {
            let output = run_executor(vec!["--state".to_owned(), state_path().display().to_string(), "--local-task-approve".to_owned(), task_id])?;
            if !output.is_empty() {
                start_local_runner()?;
                return Ok(());
            }
        } else {
            run_executor(vec!["--state".to_owned(), state_path().display().to_string(), "--local-task-cancel".to_owned(), task_id])?;
            return Ok(());
        }
        return Err("The private local task could not be queued.".to_owned());
    }
    let flag = if approved { "--approve-task" } else { "--deny-task" };
    run_executor(vec!["--state".to_owned(), state_path().display().to_string(), flag.to_owned(), task_id])?;
    Ok(())
}

fn local_chat_endpoint(base_url: &str) -> String {
    let trimmed = base_url.trim_end_matches('/');
    if trimmed.ends_with("/chat/completions") { trimmed.to_owned() } else { format!("{trimmed}/chat/completions") }
}

fn local_delta_text(value: &Value) -> Option<String> {
    value.get("choices")?.as_array()?.first()?.get("delta").and_then(|delta| delta.get("content")).and_then(Value::as_str).map(str::to_owned)
        .or_else(|| value.get("choices")?.as_array()?.first()?.get("message").and_then(|message| message.get("content")).and_then(Value::as_str).map(str::to_owned))
}

fn local_event_payload(line: &str) -> Option<&str> {
    let trimmed = line.trim();
    if trimmed.is_empty() || trimmed.starts_with(':') {
        return None;
    }
    // Accept both standard SSE frames and a plain JSON line. Some
    // OpenAI-compatible gateways ignore stream=true and return one JSON
    // object (often followed by a newline) instead of `data:` frames.
    trimmed.strip_prefix("data:").map(str::trim).or_else(|| trimmed.starts_with('{').then_some(trimmed))
}

fn emit_local_payload(app: &AppHandle, data: &str) -> Result<bool, String> {
    if data == "[DONE]" {
        return Ok(false);
    }
    let value = serde_json::from_str::<Value>(data).map_err(|_| "Local provider returned invalid JSON.".to_owned())?;
    if let Some(text) = local_delta_text(&value).filter(|text| !text.is_empty()) {
        app.emit("smara-chat-event", json!({"type": "token", "text": text})).map_err(|error| error.to_string())?;
        return Ok(true);
    }
    Ok(false)
}

async fn try_local_agent_turn(app: &AppHandle, args: &ChatArgs, profile: &LocalModelProfile, secret: &str) -> Result<Option<()>, String> {
    let connection = current_connection();
    if connection.capabilities.is_empty() {
        return Ok(None);
    }
    let endpoint = local_chat_endpoint(&profile.base_url);
    let capability_descriptions = connection.capabilities.iter().map(|capability| match capability.as_str() {
        "local_file_read" => "local_file_read: read_file, list_tree, search_text, find_files, git_summary, workspace_snapshot",
        "local_file_write" => "local_file_write: preview_only, write, append, patch, rename, move, delete, undo, create/edit DOCX/XLSX/PPTX/PDF",
        "local_terminal" => "local_terminal: argv array plus approved cwd, or a deterministic recipe",
        "local_browser" => "local_browser: open, inspect_text, inspect_dom, download on approved domains",
        "local_integration" => "local_integration: Tavily search or GitHub list_repositories using a local credential alias",
        _ => capability,
    }).collect::<Vec<_>>().join("\n");
    let system = format!(
        "You are Smara's private desktop planner. Answer normally when no local action is needed. When the user asks to read, write, run, inspect, create a document, browse, search with a local connector, or access GitHub, you MUST call request_local_action exactly once and must not claim the action already happened. Use only the enabled capabilities below. Paths must be inside approved folders; terminal uses an argv array, never a shell string. Credentials are referenced only by alias and never included as values.\n\nEnabled capabilities:\n{capability_descriptions}"
    );
    let tool = json!({
        "type": "function",
        "function": {
            "name": "request_local_action",
            "description": "Create one approval-policy-controlled task on this Desktop using an enabled local capability.",
            "parameters": {
                "type": "object",
                "additionalProperties": false,
                "required": ["title", "objective", "capability", "payload"],
                "properties": {
                    "title": {"type": "string", "maxLength": 160},
                    "objective": {"type": "string", "maxLength": 8000},
                    "capability": {"type": "string", "enum": connection.capabilities},
                    "payload": {"type": "object", "description": "Capability-specific payload validated again by the local executor."}
                }
            }
        }
    });
    let payload = json!({
        "model": profile.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": args.message}],
        "tools": [tool],
        "tool_choice": "auto",
        "parallel_tool_calls": false,
        "stream": false,
        "max_tokens": 2048,
        "temperature": 0.1,
    });
    let client = shared_http_client();
    let mut request = client.post(endpoint).timeout(std::time::Duration::from_secs(300)).json(&payload);
    if profile.auth_header == "api-subscription-key" { request = request.header("api-subscription-key", secret); }
    else { request = request.bearer_auth(secret); }
    let response = request.send().await.map_err(|error| format!("Could not reach the private {} provider: {error}", profile.label))?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED || response.status() == reqwest::StatusCode::FORBIDDEN {
        return Err(format!("{} rejected the local API key. Update this provider in Settings.", profile.label));
    }
    if matches!(response.status().as_u16(), 400 | 404 | 422) {
        // Some OpenAI-compatible endpoints do not implement tools. Preserve
        // ordinary private chat through the existing streaming fallback.
        return Ok(None);
    }
    if !response.status().is_success() {
        return Err(format!("Private {} provider returned HTTP {}. Check its endpoint and model.", profile.label, response.status()));
    }
    let value = response.json::<Value>().await.map_err(|_| "The private model returned invalid JSON.".to_owned())?;
    let message = value.get("choices").and_then(Value::as_array).and_then(|items| items.first()).and_then(|choice| choice.get("message"));
    let Some(message) = message else { return Ok(None); };
    let tool_call = message.get("tool_calls").and_then(Value::as_array).and_then(|items| items.first());
    if let Some(call) = tool_call {
        let function = call.get("function").unwrap_or(call);
        if function.get("name").and_then(Value::as_str) != Some("request_local_action") {
            return Err("The private model requested an unknown local tool.".to_owned());
        }
        let raw_arguments = function.get("arguments").and_then(Value::as_str).ok_or_else(|| "The private model returned invalid local tool arguments.".to_owned())?;
        let arguments: Value = serde_json::from_str(raw_arguments).map_err(|_| "The private model returned malformed local tool arguments.".to_owned())?;
        app.emit("smara-chat-event", json!({"type": "phase", "phase": "local_plan"})).map_err(|error| error.to_string())?;
        app.emit("smara-chat-event", json!({"type": "tool_call", "name": arguments.get("capability").and_then(Value::as_str).unwrap_or("local_action")})).map_err(|error| error.to_string())?;
        let task = create_private_local_task(&arguments, &args.conversation_id)?;
        let task_id = task.get("id").and_then(Value::as_str).unwrap_or("");
        let status = task.get("status").and_then(Value::as_str).unwrap_or("waiting_approval");
        let title = task.get("title").and_then(Value::as_str).unwrap_or("Local task");
        let answer = if status == "queued" {
            format!("I started **{title}** on this Desktop. Its verified result will appear here and in Activity.")
        } else {
            format!("I prepared **{title}**. Open Activity to review and approve it on this Desktop.")
        };
        app.emit("smara-chat-event", json!({"type": "tool_result", "name": "local_task", "ok": true, "preview": status})).map_err(|error| error.to_string())?;
        app.emit("smara-chat-event", json!({"type": "token", "text": answer})).map_err(|error| error.to_string())?;
        app.emit("smara-chat-event", json!({"type": "done", "tools_used": 1, "task_id": task_id})).map_err(|error| error.to_string())?;
        return Ok(Some(()));
    }
    if let Some(content) = message.get("content").and_then(Value::as_str).filter(|text| !text.trim().is_empty()) {
        app.emit("smara-chat-event", json!({"type": "phase", "phase": "answer"})).map_err(|error| error.to_string())?;
        app.emit("smara-chat-event", json!({"type": "token", "text": content})).map_err(|error| error.to_string())?;
        app.emit("smara-chat-event", json!({"type": "done", "tools_used": 0})).map_err(|error| error.to_string())?;
        return Ok(Some(()));
    }
    Ok(None)
}

async fn stream_local_chat(app: AppHandle, args: &ChatArgs, profile: &LocalModelProfile) -> Result<(), String> {
    let secret = resolve_local_secret(&profile.credential_name)?;
    if current_connection().runtime_mode == "local" {
        if try_local_agent_turn(&app, args, profile, &secret).await?.is_some() {
            return Ok(());
        }
    }
    let endpoint = local_chat_endpoint(&profile.base_url);
    let payload = json!({
        "model": profile.model,
        "messages": [
            {"role": "system", "content": "You are Smara running privately on the user's desktop. Be concise, useful, and clear about limits. Do not claim to have run tools or changed files."},
            {"role": "user", "content": args.message},
        ],
        "stream": true,
        "max_tokens": 2048,
        "temperature": 0.2,
    });
    let client = shared_http_client();
    let mut request = client.post(endpoint).timeout(std::time::Duration::from_secs(300)).header("Accept", "text/event-stream").json(&payload);
    if profile.auth_header == "api-subscription-key" { request = request.header("api-subscription-key", &secret); }
    else { request = request.bearer_auth(&secret); }
    let response = request.send().await.map_err(|error| format!("Could not reach the local {0} provider: {error}", profile.label))?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED || response.status() == reqwest::StatusCode::FORBIDDEN { return Err(format!("{0} rejected the local API key. Update this provider in Settings.", profile.label)); }
    if !response.status().is_success() { return Err(format!("Local {} provider returned HTTP {}. Check its endpoint and model.", profile.label, response.status())); }
    app.emit("smara-chat-event", json!({"type": "phase", "phase": "answer"})).map_err(|error| error.to_string())?;
    let mut stream = response.bytes_stream();
    let mut buffer = String::new();
    let mut emitted = false;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|error| format!("Local provider stream disconnected: {error}"))?;
        buffer.push_str(&String::from_utf8_lossy(&chunk));
        while let Some(index) = buffer.find('\n') {
            let line = buffer[..index].trim_end_matches('\r').to_owned();
            buffer.drain(..=index);
            if let Some(data) = local_event_payload(&line) {
                emitted |= emit_local_payload(&app, data)?;
            }
        }
    }
    // A few OpenAI-compatible gateways ignore stream=true and return one JSON
    // object. Accept that response without making users retry their message.
    if !emitted && !buffer.trim().is_empty() {
        if let Some(raw) = local_event_payload(buffer.trim()) {
            emitted |= emit_local_payload(&app, raw)?;
        }
    }
    if !emitted {
        return Err(format!("{} returned no visible answer. Check the model name and token limit.", profile.label));
    }
    app.emit("smara-chat-event", json!({"type": "done", "tools_used": 0})).map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
async fn stream_chat(app: AppHandle, args: ChatArgs) -> Result<(), String> {
    if args.message.trim().is_empty() { return Err("Message cannot be empty.".to_owned()); }
    if current_connection().runtime_mode == "local" && !args.model_profile.starts_with("local:") {
        return Err("Local-first mode needs a private Desktop model. Choose one in Settings, or switch Runtime mode to Hosted + Desktop.".to_owned());
    }
    if let Some(profile_id) = args.model_profile.strip_prefix("local:") {
        let profiles = stored_local_model_profiles();
        let profile = profiles.iter().find(|profile| profile.id == profile_id).ok_or_else(|| "This local model profile no longer exists. Choose another model in Settings.".to_owned())?;
        return stream_local_chat(app, &args, profile).await;
    }
    let token = cli_token()?;
    let mut payload = json!({ "message": args.message, "workspace_id": if args.workspace.trim().is_empty() { "default" } else { args.workspace.trim() }, "conversation_id": args.conversation_id });
    if !args.model_profile.trim().is_empty() && args.model_profile.trim() != "default" { payload["model_profile"] = Value::String(args.model_profile.trim().to_owned()); }
    let client = shared_http_client();
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
        .invoke_handler(tauri::generate_handler![load_connection, save_settings, check_connection, login_cli, pair_desktop, start_executor, stop_executor, pause_executor, resume_executor, revoke_executor, read_log, load_tasks, load_task_details, decide_local_task, stream_chat, open_web, list_local_credentials, save_local_credential, delete_local_credential, list_local_connectors, revoke_local_connector, list_local_model_profiles, save_local_model_profile, delete_local_model_profile])
        .run(tauri::generate_context!())
        .expect("error while running Smara Desktop");
}

#[cfg(test)]
mod tests {
    use super::{derived_local_capabilities, local_delta_text, local_event_payload, normalized_api_url, normalized_pairing_code, normalized_web_url, preserve_local_model_profiles};
    use serde_json::json;

    #[test]
    fn local_stream_accepts_sse_and_plain_json_lines() {
        assert_eq!(local_event_payload("data: {\"choices\":[]}"), Some("{\"choices\":[]}"));
        assert_eq!(local_event_payload("{\"choices\":[]}"), Some("{\"choices\":[]}"));
        assert_eq!(local_event_payload(": keepalive"), None);
    }

    #[test]
    fn local_stream_reads_message_fallback_without_reasoning_text() {
        let value = json!({"choices":[{"message":{"content":"hello","reasoning_content":"private"}}]});
        assert_eq!(local_delta_text(&value).as_deref(), Some("hello"));
    }

    #[test]
    fn local_permissions_derive_only_explicitly_enabled_capabilities() {
        let capabilities = derived_local_capabilities(
            &["C:\\approved".to_owned()],
            &["python".to_owned()],
            &["github.com".to_owned()],
        );
        assert!(capabilities.contains(&"local_file_read".to_owned()));
        assert!(capabilities.contains(&"local_file_write".to_owned()));
        assert!(capabilities.contains(&"local_terminal".to_owned()));
        assert!(capabilities.contains(&"local_browser".to_owned()));
    }

    #[test]
    fn repairs_legacy_smara_mount_without_dropping_a_dev_port() {
        assert_eq!(
            normalized_web_url("http://localhost:3000/smara-api", "http://localhost:3000/smara/"),
            "http://localhost:3000/"
        );
    }

    #[test]
    fn leaves_custom_web_paths_untouched() {
        assert_eq!(
            normalized_web_url("https://ai.syntarus.com/smara-api", "https://ai.syntarus.com/custom/"),
            "https://ai.syntarus.com/custom/"
        );
    }

    #[test]
    fn repairs_legacy_public_web_root_used_as_api_url() {
        assert_eq!(
            normalized_api_url("https://ai.syntarus.com/"),
            "https://ai.syntarus.com/smara-api"
        );
        assert_eq!(
            normalized_api_url("https://ai.syntarus.com/smara/"),
            "https://ai.syntarus.com/smara-api"
        );
    }

    #[test]
    fn keeps_custom_api_origins_unchanged() {
        assert_eq!(
            normalized_api_url("http://localhost:8090/"),
            "http://localhost:8090"
        );
        assert_eq!(
            normalized_api_url("https://example.test/custom-api/"),
            "https://example.test/custom-api"
        );
    }

    #[test]
    fn pairing_code_normalizes_copied_whitespace_and_case() {
        assert_eq!(normalized_pairing_code(" 9aea 8e4f\r\n"), "9AEA8E4F");
        assert_eq!(normalized_pairing_code("9AEA8E4F-extra"), "9AEA8E4F");
        assert_eq!(normalized_pairing_code("9AEA8E4"), "9AEA8E4");
    }

    #[test]
    fn general_settings_keep_private_model_metadata() {
        let value = json!({"model_profile": "local:sarvam", "workspace": "default"});
        let profiles = json!([{"id": "sarvam", "label": "Sarvam"}]);
        let merged = preserve_local_model_profiles(value, Some(profiles.clone()));
        assert_eq!(merged.get("local_model_profiles"), Some(&profiles));
    }

    #[test]
    fn general_settings_do_not_invent_private_model_metadata() {
        let value = json!({"model_profile": "default"});
        let merged = preserve_local_model_profiles(value, None);
        assert!(merged.get("local_model_profiles").is_none());
    }
}
