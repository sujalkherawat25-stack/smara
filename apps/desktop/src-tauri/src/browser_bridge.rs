//! Smara Browser Bridge, driven by Desktop.
//!
//! The helper (`smara_bridge.py`, bundled unchanged) is a detached background program that starts with Windows,
//! restarts itself after crashes and keeps its pairing in `~/.smara-bridge/config.json`. That file is the single source
//! of truth for the device token. Desktop never shows a terminal: it calls the helper's own commands (`ensure`,
//! `pair`, `install-startup`, `stop`, `unpair`) and reads its pid file and log to show status.
use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::{Command, Stdio};

const DEFAULT_SERVER: &str = "https://ai.syntarus.com/smara";
const RUN_VALUE: &str = "Smara Browser Bridge";
const LOG_LINES: usize = 80;

fn home() -> PathBuf {
    if let Some(path) = std::env::var_os("SMARA_BRIDGE_HOME") { return PathBuf::from(path); }
    let base = PathBuf::from(std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).unwrap_or_default());
    #[cfg(not(test))]
    {
        carry_over_old_home(&base);
    }
    base.join(".smara-bridge")
}
/// Before the rename the pairing lived in `~/.munim-bridge`. Copy it once so a paired PC stays paired, then leave a
/// marker in the old folder so a later unpair cannot bring the pairing back. Tests never touch the real folder.
#[cfg(not(test))]
fn carry_over_old_home(base: &std::path::Path) {
    let old = base.join(".munim-bridge");
    let new = base.join(".smara-bridge");
    if !old.is_dir() || old.join("MOVED_TO_SMARA").exists() { return; }
    if !new.exists() { let _ = copy_dir(&old, &new); }
    let _ = std::fs::write(old.join("MOVED_TO_SMARA"), "Copied to ~/.smara-bridge. Smara no longer uses this folder.\n");
}
#[cfg(not(test))]
fn copy_dir(from: &std::path::Path, to: &std::path::Path) -> std::io::Result<()> {
    std::fs::create_dir_all(to)?;
    for entry in std::fs::read_dir(from)? {
        let entry = entry?;
        let target = to.join(entry.file_name());
        if entry.file_type()?.is_dir() { copy_dir(&entry.path(), &target)?; } else { std::fs::copy(entry.path(), target)?; }
    }
    Ok(())
}
fn hide(command: &mut Command) {
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    #[cfg(not(windows))] { let _ = command; }
}
fn bridge_script() -> Option<PathBuf> {
    let installed = std::env::current_exe().ok()?.parent()?.join("resources/browser/smara_bridge.py");
    if installed.is_file() { return Some(installed); }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/browser/smara_bridge.py");
    dev.is_file().then_some(dev)
}
fn python() -> Command {
    let mut command = if let Some(p) = std::env::var_os("SMARA_BRIDGE_PYTHON") { Command::new(p) }
        else if cfg!(windows) { let mut c = Command::new("py"); c.arg("-3"); c } else { Command::new("python3") };
    command.env("PYTHONIOENCODING", "utf-8").env("PYTHONUTF8", "1").stdin(Stdio::null());
    hide(&mut command);
    command
}

// ---- pure helpers (unit-tested) ---------------------------------------------------------------------------
pub fn normalize_server(raw: &str) -> Result<String, String> {
    let value = raw.trim().trim_end_matches('/');
    let (scheme, rest) = value.split_once("://").ok_or("Enter the full server address, for example https://ai.syntarus.com/smara")?;
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
/// (server, shop, has_token) from the helper's config.json. The token itself is never returned.
pub fn parse_config(text: &str) -> Option<(String, String, bool)> {
    let value: Value = serde_json::from_str(text).ok()?;
    Some((value["server"].as_str().unwrap_or_default().to_owned(), value["shop"].as_str().unwrap_or_default().to_owned(),
          value["token"].as_str().map(|t| !t.is_empty()).unwrap_or(false)))
}
/// True if `tasklist /FO CSV /NH` output lists this pid.
pub fn tasklist_has(output: &str, pid: u32) -> bool {
    let needle = format!("\"{pid}\"");
    output.lines().any(|line| line.contains(&needle))
}
/// The helper's heartbeat (status.json). It never contains the token; keep only the fields the panel shows.
pub fn parse_status(text: &str) -> Value {
    let Ok(value) = serde_json::from_str::<Value>(text) else { return Value::Null };
    // `online` stays true after the first successful poll, so the UI must also check `last_poll_at` for freshness.
    json!({"last_poll_at": value["last_poll_at"].as_f64().unwrap_or(0.0), "last_step": value["last_step"].as_str().unwrap_or_default(),
           "last_step_at": value["last_step_at"].as_f64().unwrap_or(0.0), "online": value["online"].as_bool().unwrap_or(false)})
}
/// The paired shop's numbers the helper saved (overview.json). Only the fields the Hosted panel shows are kept.
pub fn parse_overview(text: &str) -> Value {
    let Ok(v) = serde_json::from_str::<Value>(text) else { return Value::Null };
    if !v.is_object() { return Value::Null; }
    let n = |x: &Value| x.as_i64().unwrap_or(0);
    json!({
        "shop": v["shop"].as_str().unwrap_or_default(),
        "dues": {"open": n(&v["dues"]["open"]), "open_paise": n(&v["dues"]["open_paise"]),
                 "overdue": n(&v["dues"]["overdue"]), "overdue_paise": n(&v["dues"]["overdue_paise"])},
        "payables": {"open": n(&v["payables"]["open"]), "open_paise": n(&v["payables"]["open_paise"])},
        "waiting_for_owner": n(&v["waiting_for_owner"]),
        "channels": {"telegram_linked": v["channels"]["telegram_linked"].as_bool().unwrap_or(false),
                     "whatsapp_window_open": v["channels"]["whatsapp_window_open"].as_bool().unwrap_or(false),
                     "last_used": v["channels"]["last_used"].as_str().unwrap_or_default()},
        "tally_last_sync_at": v["tally_last_sync_at"].as_str(),
        "fetched_at": v["fetched_at"].as_f64().unwrap_or(0.0),
    })
}
pub fn tail_lines(text: &str, count: usize) -> Vec<String> {
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    lines[lines.len().saturating_sub(count)..].iter().map(|l| (*l).to_owned()).collect()
}

// ---- state ------------------------------------------------------------------------------------------------
fn read_config() -> Option<(String, String, bool)> { parse_config(&std::fs::read_to_string(home().join("config.json")).ok()?) }
fn helper_pid() -> Option<u32> {
    let pid: u32 = std::fs::read_to_string(home().join("bridge.pid")).ok()?.trim().parse().ok()?;
    if cfg!(windows) {
        let mut command = Command::new("tasklist");
        command.args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"]).stdin(Stdio::null());
        hide(&mut command);
        let output = command.output().ok()?;
        tasklist_has(&String::from_utf8_lossy(&output.stdout), pid).then_some(pid)
    } else {
        Command::new("kill").args(["-0", &pid.to_string()]).status().ok().filter(|s| s.success()).map(|_| pid)
    }
}
/// The Windows startup command for the helper, or None if it is not registered.
fn startup_command() -> Option<String> {
    if !cfg!(windows) { return None; }
    let mut command = Command::new("reg");
    command.args(["query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run", "/v", RUN_VALUE]).stdin(Stdio::null()).stderr(Stdio::null());
    hide(&mut command);
    let output = command.output().ok().filter(|o| o.status.success())?;
    parse_run_value(&String::from_utf8_lossy(&output.stdout))
}
/// Data of `reg query` output: `    Name    REG_SZ    <data>`.
pub fn parse_run_value(output: &str) -> Option<String> {
    output.lines().find_map(|line| line.split_once("REG_SZ").map(|(_, data)| data.trim().to_owned())).filter(|v| !v.is_empty())
}
/// True when the startup entry already launches this exact script (case-insensitive, slashes normalised).
pub fn startup_uses(command: &str, script: &std::path::Path) -> bool {
    let norm = |t: &str| t.replace('/', "\\").to_ascii_lowercase();
    norm(command).contains(&norm(&script.to_string_lossy()))
}
fn startup_registered() -> bool { startup_command().is_some() || !cfg!(windows) }
fn snapshot() -> Value {
    let config = read_config();
    let paired = config.as_ref().map(|c| c.2).unwrap_or(false);
    let log = std::fs::read_to_string(home().join("bridge.log")).map(|t| tail_lines(&t, LOG_LINES)).unwrap_or_default();
    json!({
        "paired": paired, "server": config.as_ref().map(|c| c.0.clone()).filter(|s| !s.is_empty()).unwrap_or_else(|| DEFAULT_SERVER.into()),
        "shop": config.as_ref().map(|c| c.1.clone()).unwrap_or_default(), "running": paired && helper_pid().is_some(),
        "startup": startup_registered(), "log": log, "paused": home().join("paused").exists(),
        "status": std::fs::read_to_string(home().join("status.json")).map(|t| parse_status(&t)).unwrap_or(Value::Null),
        "overview": if paired { std::fs::read_to_string(home().join("overview.json")).map(|t| parse_overview(&t)).unwrap_or(Value::Null) } else { Value::Null },
        "now": std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0), "default_server": DEFAULT_SERVER,
    })
}

/// Run one helper command to completion and return its trimmed output.
fn helper(args: &[&str]) -> Result<(bool, String), String> {
    let script = bridge_script().ok_or("The bridge component is missing from this install")?;
    let output = python().arg(&script).args(args).stdout(Stdio::piped()).stderr(Stdio::piped()).output()
        .map_err(|_| "Python 3 was not found. Open Settings → Browser and press “Install browser components”, or install Python 3 from python.org once.".to_owned())?;
    let mut text = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let err = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() && text.is_empty() { text = err.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("The helper failed").to_owned(); }
    Ok((output.status.success(), text))
}
fn components() -> Value {
    let has_python = python().arg("--version").stdout(Stdio::null()).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false);
    let has_modules = has_python && python().args(["-c", "import playwright, httpx"]).stdout(Stdio::null()).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false);
    let found = |paths: &[&str]| paths.iter().any(|p| std::path::Path::new(p).is_file());
    json!({"python": has_python, "modules": has_modules, "browsers": {
        "brave": found(&[r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe", r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe"]),
        "chrome": found(&[r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]),
        "edge": found(&[r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]) }})
}
/// Start the helper if this PC is paired and it is not running; make it start with Windows. Safe to call repeatedly.
fn ensure_inner() -> Result<(), String> {
    if !snapshot()["paired"].as_bool().unwrap_or(false) { return Ok(()); }
    // One canonical copy: the startup entry must launch the bundled helper, so a reboot never brings back an older one.
    let current = bridge_script().map(|script| startup_command().map(|command| startup_uses(&command, &script)).unwrap_or(false)).unwrap_or(true);
    if !current { helper(&["install-startup"])?; }        // rewrites the entry to the bundled script; starts it only if not running
    else if helper_pid().is_none() { helper(&["ensure"])?; }
    Ok(())
}
/// Called once when Desktop opens. Failure is silent: the Browser tab explains what is missing.
pub fn ensure_on_startup() { std::thread::spawn(|| { let _ = ensure_inner(); }); }

// ---- commands ---------------------------------------------------------------------------------------------
#[tauri::command]
pub async fn bridge_state() -> Result<Value, String> { tauri::async_runtime::spawn_blocking(snapshot).await.map_err(|e| e.to_string()) }

#[tauri::command]
pub async fn bridge_check() -> Result<Value, String> { tauri::async_runtime::spawn_blocking(components).await.map_err(|e| e.to_string()) }

#[tauri::command]
pub async fn bridge_ensure() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| { ensure_inner()?; std::thread::sleep(std::time::Duration::from_millis(1500)); Ok(snapshot()) }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn bridge_stop() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| { let (_, _) = helper(&["stop"])?; Ok(snapshot()) }).await.map_err(|e| e.to_string())?
}

/// Pause/Resume is a file the helper checks before every step; the helper's own `pause`/`resume` commands write the same file.
#[tauri::command]
pub async fn bridge_pause(paused: bool) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let flag = home().join("paused");
        if paused { std::fs::create_dir_all(home()).and_then(|_| std::fs::write(&flag, "paused")).map_err(|e| format!("Could not pause: {e}"))?; }
        else { match std::fs::remove_file(&flag) { Ok(()) => {}, Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}, Err(e) => return Err(format!("Could not resume: {e}")) } }
        Ok(snapshot())
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn bridge_pair(server: String, code: String) -> Result<Value, String> {
    let server = normalize_server(&server)?;
    let code = normalize_code(&code)?;
    tauri::async_runtime::spawn_blocking(move || {
        let name = format!("{} (Smara Desktop)", std::env::var("COMPUTERNAME").unwrap_or_else(|_| "PC".into()));
        let (ok, message) = helper(&["pair", "--server", &server, "--code", &code, "--name", &name])?;
        if !ok { return Err(message.trim_start_matches("Pairing failed:").trim().to_owned()); }
        helper(&["install-startup"])?;            // one token store (config.json), starts now and with Windows
        std::thread::sleep(std::time::Duration::from_millis(1500));
        Ok(snapshot())
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn bridge_unpair() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| { helper(&["uninstall-startup"])?; helper(&["unpair"])?; Ok(snapshot()) }).await.map_err(|e| e.to_string())?
}

/// One-click install of what the helper needs (Playwright drives the browser you already have; no browser download).
#[tauri::command]
pub async fn bridge_install_components() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let output = python().args(["-m", "pip", "install", "--user", "--disable-pip-version-check", "playwright", "httpx"])
            .stdout(Stdio::piped()).stderr(Stdio::piped()).output()
            .map_err(|_| "Python 3 is not installed. Install it once from python.org (tick “Add python.exe to PATH”), then press this button again.".to_owned())?;
        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            return Err(format!("Installing the components failed: {}", err.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("unknown error")));
        }
        Ok(components())
    }).await.map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn server_must_be_https_or_local_http() {
        assert_eq!(normalize_server(" https://ai.syntarus.com/smara/ ").unwrap(), "https://ai.syntarus.com/smara");
        assert!(normalize_server("http://example.com").is_err());
        assert!(normalize_server("http://localhost:8000").is_ok());
        assert!(normalize_server("https://user@evil.com").is_err());
        assert!(normalize_server("ai.syntarus.com").is_err());
    }
    #[test]
    fn code_is_normalised() {
        assert_eq!(normalize_code("abcd efgh").unwrap(), "ABCD-EFGH");
        assert!(normalize_code("abc").is_err());
    }
    #[test]
    fn existing_pairing_is_read_without_exposing_the_token() {
        assert_eq!(parse_config(r#"{"server":"https://x/smara","token":"secret","shop":"Demo Shop"}"#), Some(("https://x/smara".into(), "Demo Shop".into(), true)));
        assert_eq!(parse_config(r#"{"server":"https://x"}"#).map(|c| c.2), Some(false));
        assert_eq!(parse_config("not json"), None);
    }
    #[test]
    fn pid_is_found_in_tasklist_output() {
        assert!(tasklist_has("\"pythonw.exe\",\"35060\",\"Console\",\"1\",\"40,000 K\"", 35060));
        assert!(!tasklist_has("INFO: No tasks are running which match the specified criteria.", 35060));
        assert!(!tasklist_has("\"python.exe\",\"350601\",\"Console\"", 35060));
    }
    #[test]
    fn heartbeat_keeps_only_display_fields() {
        let status = parse_status(r#"{"last_poll_at": 100.5, "online": true, "paused": false, "pid": 7, "token": "x", "last_step": "navigate on a.com", "last_step_at": 99.0}"#);
        assert_eq!(status["last_poll_at"], 100.5);
        assert_eq!(status["last_step"], "navigate on a.com");
        assert!(status.get("token").is_none() && status.get("pid").is_none());
        assert!(parse_status("garbage").is_null());
    }
    #[test]
    fn startup_entry_is_compared_with_the_bundled_script() {
        let out = "\nHKEY_CURRENT_USER\\Software\\X\\Run\n    Smara Browser Bridge    REG_SZ    \"C:\\Python314\\pythonw.exe\" \"C:\\Users\\sujal\\smara\\bridge\\smara_bridge.py\" \"run\"\n";
        let cmd = parse_run_value(out).unwrap();
        assert!(startup_uses(&cmd, std::path::Path::new(r"C:\Users\sujal\smara\bridge\smara_bridge.py")));
        assert!(!startup_uses(&cmd, std::path::Path::new(r"C:\Users\sujal\AppData\Local\Smara Desktop\resources\browser\smara_bridge.py")));
        assert!(startup_uses(&cmd.to_uppercase(), std::path::Path::new("c:/users/sujal/smara/bridge/smara_bridge.py")));
        assert_eq!(parse_run_value("ERROR: not found"), None);
    }
    #[test]
    fn overview_keeps_only_the_numbers_the_panel_shows() {
        let o = parse_overview(r#"{"shop":"Demo","dues":{"open":3,"open_paise":250000,"overdue":1,"overdue_paise":100000},"payables":{"open":2,"open_paise":5000},
            "waiting_for_owner":2,"channels":{"telegram_linked":true,"whatsapp_window_open":false,"last_used":"telegram"},
            "tally_last_sync_at":"2026-11-11T02:00:00+00:00","fetched_at":1700000000.0,"token":"x","device":"PC"}"#);
        assert_eq!(o["dues"]["overdue_paise"], 100000);
        assert_eq!(o["waiting_for_owner"], 2);
        assert_eq!(o["channels"]["telegram_linked"], serde_json::json!(true));
        assert_eq!(o["tally_last_sync_at"], "2026-11-11T02:00:00+00:00");
        assert!(o.get("token").is_none() && o.get("device").is_none());
        assert!(parse_overview("garbage").is_null() && parse_overview("[]").is_null());
        assert!(parse_overview("{}")["tally_last_sync_at"].is_null());
    }
    #[test]
    fn log_tail_skips_blank_lines() {
        assert_eq!(tail_lines("a\n\nb\nc\n", 2), vec!["b", "c"]);
        assert_eq!(tail_lines("", 5).len(), 0);
    }

    #[test]
    fn helper_status_keeps_online_and_the_last_poll_time_for_the_hosted_chip() {
        let parsed = parse_status(r#"{"online": true, "last_poll_at": 1700000000.5, "last_step": "read", "last_step_at": 1700000001.0}"#);
        assert_eq!(parsed["online"], serde_json::json!(true));
        assert_eq!(parsed["last_poll_at"], serde_json::json!(1700000000.5));
        assert_eq!(parse_status("not json"), Value::Null);
        assert_eq!(parse_status("{}")["online"], serde_json::json!(false));
    }
}
