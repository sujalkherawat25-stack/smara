//! Smara Browser Bridge, managed by Desktop.
//!
//! Desktop pairs with the hosted server, keeps the device token in the OS credential store (Windows Credential
//! Manager) and runs the vendored Python bridge as a sidecar. The token reaches the sidecar only through its
//! environment, never a file or argument. The sidecar reports each step as a JSON line which is forwarded to the UI.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Mutex, OnceLock};
use tauri::{AppHandle, Emitter};

const KEYRING_SERVICE: &str = "Smara Desktop";
const KEYRING_USER: &str = "browser-bridge-device-token";
const DEFAULT_SERVER: &str = "https://ai.syntarus.com/munim";
const MAX_EVENTS: usize = 300;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BridgeConfig {
    pub server: String,
    pub shop: String,
    pub device_name: String,
    /// brave | chrome | edge | auto
    pub browser: String,
    /// Empty, or a CDP URL such as http://127.0.0.1:9222
    pub attach: String,
    /// Empty means the dedicated Smara profile under the app data folder.
    pub profile: String,
    pub autostart: bool,
}
impl Default for BridgeConfig {
    fn default() -> Self {
        Self { server: DEFAULT_SERVER.into(), shop: String::new(), device_name: String::new(), browser: "brave".into(),
               attach: String::new(), profile: String::new(), autostart: false }
    }
}

struct Running { child: Child, stdin: ChildStdin, generation: u64 }
#[derive(Default)]
struct Runtime { running: Option<Running>, events: VecDeque<Value>, paused: bool, last_poll_ok_ms: u64, generation: u64, last_error: String }
fn runtime() -> &'static Mutex<Runtime> {
    static RUNTIME: OnceLock<Mutex<Runtime>> = OnceLock::new();
    RUNTIME.get_or_init(|| Mutex::new(Runtime::default()))
}
fn lock() -> std::sync::MutexGuard<'static, Runtime> { runtime().lock().unwrap_or_else(|e| e.into_inner()) }

fn data_dir() -> PathBuf {
    std::env::var_os("SMARA_DESKTOP_DATA_DIR").map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("APPDATA").unwrap_or_default()).join("Smara"))
}
fn config_path() -> PathBuf { data_dir().join("browser_bridge.json") }
fn default_profile() -> PathBuf { data_dir().join("browser-profile") }

pub fn load_config() -> BridgeConfig {
    std::fs::read(config_path()).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default()
}
fn save_config(config: &BridgeConfig) -> Result<(), String> {
    std::fs::create_dir_all(data_dir()).map_err(|e| format!("Cannot create the settings folder: {e}"))?;
    let bytes = serde_json::to_vec_pretty(config).map_err(|e| e.to_string())?;
    std::fs::write(config_path(), bytes).map_err(|e| format!("Cannot save browser settings: {e}"))
}

// ---- credential store -------------------------------------------------------------------------------------
fn token_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER).map_err(|e| format!("Credential store unavailable: {e}"))
}
fn read_token() -> Option<String> { token_entry().ok()?.get_password().ok().filter(|t| !t.is_empty()) }
fn store_token(token: &str) -> Result<(), String> {
    token_entry()?.set_password(token).map_err(|e| format!("Could not save the device token to the credential store: {e}"))
}
fn delete_token() -> Result<(), String> {
    match token_entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("Could not remove the device token from the credential store: {e}")),
    }
}

// ---- validation (pure, unit-tested) -----------------------------------------------------------------------
pub fn normalize_server(raw: &str) -> Result<String, String> {
    let value = raw.trim().trim_end_matches('/');
    let (scheme, rest) = value.split_once("://").ok_or("Enter the full server address, for example https://ai.syntarus.com/munim")?;
    let host = rest.split(['/', ':']).next().unwrap_or_default().to_ascii_lowercase();
    if host.is_empty() || rest.contains(['@', ' ', '?', '#']) { return Err("That server address is not valid".into()); }
    let local = host == "localhost" || host == "127.0.0.1";
    match scheme { "https" => {}, "http" if local => {}, _ => return Err("The server address must start with https:// (http:// is allowed only for localhost testing)".into()) }
    Ok(value.to_owned())
}
pub fn normalize_code(raw: &str) -> Result<String, String> {
    let compact: String = raw.chars().filter(|c| c.is_ascii_alphanumeric()).collect::<String>().to_ascii_uppercase();
    if compact.len() != 8 { return Err("The pairing code looks like ABCD-EFGH (8 characters). Send 'pair browser' to Smara in chat to get one".into()); }
    Ok(format!("{}-{}", &compact[..4], &compact[4..]))
}
pub fn validate_options(c: &BridgeConfig) -> Result<(), String> {
    if !matches!(c.browser.as_str(), "brave" | "chrome" | "edge" | "auto") { return Err("Unknown browser choice".into()); }
    if !c.attach.is_empty() {
        let rest = c.attach.strip_prefix("http://").ok_or("Attach address must look like http://127.0.0.1:9222")?;
        let (host, port) = rest.trim_end_matches('/').split_once(':').ok_or("Attach address needs a port, e.g. http://127.0.0.1:9222")?;
        if !(host == "127.0.0.1" || host == "localhost") || port.parse::<u16>().is_err() {
            return Err("Attach only works with a browser on this PC: http://127.0.0.1:<port>".into());
        }
    }
    if !c.profile.is_empty() && !std::path::Path::new(&c.profile).is_absolute() { return Err("Profile folder must be an absolute path".into()); }
    Ok(())
}

// ---- sidecar location -------------------------------------------------------------------------------------
fn bridge_script() -> Option<PathBuf> {
    let installed = std::env::current_exe().ok()?.parent()?.join("resources/browser/smara_desktop_bridge.py");
    if installed.is_file() { return Some(installed); }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/browser/smara_desktop_bridge.py");
    dev.is_file().then_some(dev)
}
fn python_command() -> Command {
    let mut command = if let Some(p) = std::env::var_os("SMARA_BRIDGE_PYTHON") { Command::new(p) }
        else if cfg!(windows) { let mut c = Command::new("py"); c.arg("-3"); c } else { Command::new("python3") };
    command.env("PYTHONIOENCODING", "utf-8").env("PYTHONUTF8", "1");
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    command
}

fn push_event(app: &AppHandle, mut event: Value) {
    {
        let mut state = lock();
        match event["kind"].as_str() {
            Some("paused") => state.paused = event["paused"].as_bool().unwrap_or(false),
            Some("poll") if event["ok"].as_bool() == Some(true) => state.last_poll_ok_ms = event["ts"].as_u64().unwrap_or(0),
            Some("fatal") => state.last_error = event["message"].as_str().unwrap_or_default().to_owned(),
            _ => {}
        }
        if event["kind"] != "poll" || event["ok"] == false {   // successful polls only move the online light; they are not feed items
            if state.events.len() >= MAX_EVENTS { state.events.pop_front(); }
            state.events.push_back(event.clone());
        }
    }
    if event["kind"] == "poll" && event["ok"] == true { event["silent"] = json!(true); }
    let _ = app.emit("browser-bridge-event", event);
}

// ---- commands ---------------------------------------------------------------------------------------------
fn snapshot() -> Value {
    let config = load_config();
    let paired = read_token().is_some() && !config.shop.is_empty();
    let mut state = lock();
    let running = match state.running.as_mut() {
        Some(r) => match r.child.try_wait() { Ok(None) => true, _ => false },
        None => false,
    };
    json!({
        "paired": paired, "config": config, "running": running, "paused": running && state.paused,
        "last_poll_ok_ms": if running { state.last_poll_ok_ms } else { 0 },
        "last_error": state.last_error, "events": state.events.iter().cloned().collect::<Vec<_>>(),
        "default_server": DEFAULT_SERVER, "default_profile": default_profile(),
    })
}
#[tauri::command]
pub fn bridge_state() -> Value { snapshot() }

#[tauri::command]
pub async fn bridge_pair(server: String, code: String, name: Option<String>) -> Result<Value, String> {
    let server = normalize_server(&server)?;
    let code = normalize_code(&code)?;
    let device = name.filter(|n| !n.trim().is_empty()).unwrap_or_else(|| std::env::var("COMPUTERNAME").unwrap_or_else(|_| "Smara Desktop".into()));
    let client = reqwest::Client::builder().timeout(std::time::Duration::from_secs(30)).build().map_err(|e| e.to_string())?;
    let response = client.post(format!("{server}/bridge/pair")).json(&json!({"code": code, "name": format!("{device} (Smara Desktop)")}))
        .send().await.map_err(|e| format!("Could not reach the Smara server: {e}"))?;
    let status = response.status();
    let body: Value = response.json().await.unwrap_or(Value::Null);
    if !status.is_success() {
        return Err(body["detail"].as_str().map(str::to_owned).unwrap_or_else(|| format!("Pairing failed (HTTP {status})")));
    }
    let token = body["device_token"].as_str().filter(|t| !t.is_empty()).ok_or("The server did not return a device token")?;
    let shop = body["shop"].as_str().unwrap_or("your shop").to_owned();
    store_token(token)?;
    let mut config = load_config();
    config.server = server; config.shop = shop; config.device_name = device;
    save_config(&config)?;
    Ok(snapshot())
}

#[tauri::command]
pub fn bridge_save_options(config: BridgeConfig) -> Result<Value, String> {
    validate_options(&config)?;
    let mut current = load_config();
    current.browser = config.browser; current.attach = config.attach.trim_end_matches('/').to_owned();
    current.profile = config.profile; current.autostart = config.autostart;
    save_config(&current)?;
    Ok(snapshot())
}

fn stop_inner(wait: bool) {
    let running = lock().running.take();
    if let Some(mut r) = running {
        let _ = writeln!(r.stdin, "{}", json!({"cmd": "stop"}));
        drop(r.stdin);
        if wait {
            for _ in 0..30 { if matches!(r.child.try_wait(), Ok(Some(_))) { return; } std::thread::sleep(std::time::Duration::from_millis(100)); }
        }
        let _ = r.child.kill(); let _ = r.child.wait();
    }
    let mut state = lock(); state.paused = false; state.last_poll_ok_ms = 0;
}
pub fn stop_on_exit() { stop_inner(false); }

#[tauri::command]
pub async fn bridge_stop() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| stop_inner(true)).await.map_err(|e| e.to_string())?;
    Ok(snapshot())
}

#[tauri::command]
pub async fn bridge_start(app: AppHandle) -> Result<Value, String> {
    let config = load_config();
    validate_options(&config)?;
    let token = read_token().ok_or("Not paired: pair this PC first")?;
    if snapshot()["running"] == true { return Ok(snapshot()); }
    lock().running = None;   // a previous run that already exited
    let script = bridge_script().ok_or("The bridge component is missing from this install")?;
    let profile = if config.profile.is_empty() { default_profile() } else { PathBuf::from(&config.profile) };
    let mut command = python_command();
    command.arg(&script).args(["--browser", &config.browser, "--profile", &profile.to_string_lossy()]);
    if !config.attach.is_empty() { command.args(["--attach", &config.attach]); }
    command.env("SMARA_BRIDGE_SERVER", &config.server).env("SMARA_BRIDGE_TOKEN", token).env("SMARA_BRIDGE_SHOP", &config.shop)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = command.spawn().map_err(|e| format!("Could not start Python (install Python 3 and run: pip install playwright httpx): {e}"))?;
    let stdin = child.stdin.take().ok_or("Bridge stdin unavailable")?;
    let stdout = child.stdout.take().ok_or("Bridge stdout unavailable")?;
    let stderr = child.stderr.take();
    let generation = { let mut s = lock(); s.generation += 1; s.last_error.clear(); s.paused = false; s.generation };
    lock().running = Some(Running { child, stdin, generation });
    let out_app = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok).take(1_000_000) {
            if let Ok(event) = serde_json::from_str::<Value>(&line) { push_event(&out_app, event); }
        }
        // Process ended: report once, unless a newer run has already replaced it.
        let current = { lock().generation };
        if current == generation {
            let code = { let mut s = lock(); let code = s.running.as_mut().and_then(|r| r.child.wait().ok()).and_then(|c| c.code()); if s.running.as_ref().map(|r| r.generation) == Some(generation) { s.running = None; } code };
            push_event(&out_app, json!({"kind": "exited", "ts": now_ms(), "code": code}));
        }
    });
    if let Some(err) = stderr {
        let err_app = app.clone();
        std::thread::spawn(move || {
            let mut last = String::new();
            for line in BufReader::new(err).lines().map_while(Result::ok).take(2000) { if !line.trim().is_empty() { last = line; } }
            if !last.is_empty() { push_event(&err_app, json!({"kind": "stderr", "ts": now_ms(), "message": last.chars().take(300).collect::<String>()})); }
        });
    }
    Ok(snapshot())
}
fn now_ms() -> u64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0) }

#[tauri::command]
pub fn bridge_pause(paused: bool) -> Result<Value, String> {
    {
        let mut state = lock();
        let r = state.running.as_mut().ok_or("The bridge is not running")?;
        writeln!(r.stdin, "{}", json!({"cmd": if paused { "pause" } else { "resume" }})).map_err(|_| "The bridge is not responding")?;
        r.stdin.flush().ok();
        state.paused = paused;   // optimistic; the sidecar confirms with a "paused" event
    }
    Ok(snapshot())
}

#[tauri::command]
pub async fn bridge_unpair() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| stop_inner(true)).await.map_err(|e| e.to_string())?;
    delete_token()?;
    let mut config = load_config();
    config.shop.clear(); config.device_name.clear(); config.autostart = false;
    save_config(&config)?;
    lock().events.clear();
    Ok(snapshot())
}

/// Python / Playwright / browser availability, so the panel can say what is missing instead of failing silently.
#[tauri::command]
pub async fn bridge_check() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let script = bridge_script().ok_or("The bridge component is missing from this install")?;
        let output = python_command().arg(&script).arg("--check").output()
            .map_err(|_| "Python 3 was not found. Install Python 3, then run: pip install playwright httpx".to_owned())?;
        let text = String::from_utf8_lossy(&output.stdout);
        let line = text.lines().rev().find(|l| l.trim_start().starts_with('{'));
        let mut value: Value = line.and_then(|l| serde_json::from_str(l).ok())
            .ok_or_else(|| "Python is installed, but the bridge needs: pip install playwright httpx".to_owned())?;
        value["python"] = json!(true);
        Ok(value)
    }).await.map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn server_must_be_https_or_local_http() {
        assert_eq!(normalize_server(" https://ai.syntarus.com/munim/ ").unwrap(), "https://ai.syntarus.com/munim");
        assert!(normalize_server("http://example.com").is_err());
        assert!(normalize_server("http://localhost:8000").is_ok());
        assert!(normalize_server("https://user@evil.com").is_err());
        assert!(normalize_server("ai.syntarus.com").is_err());
    }
    #[test]
    fn code_is_normalised() {
        assert_eq!(normalize_code("abcd efgh").unwrap(), "ABCD-EFGH");
        assert_eq!(normalize_code("abcd-efgh").unwrap(), "ABCD-EFGH");
        assert!(normalize_code("abc").is_err());
    }
    #[test]
    fn attach_is_local_only() {
        let mut c = BridgeConfig::default();
        c.attach = "http://127.0.0.1:9222".into();
        assert!(validate_options(&c).is_ok());
        c.attach = "http://192.168.1.5:9222".into();
        assert!(validate_options(&c).is_err());
        c.attach = String::new(); c.browser = "firefox".into();
        assert!(validate_options(&c).is_err());
    }
}
