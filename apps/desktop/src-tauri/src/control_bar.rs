//! The "Smara is controlling your PC" bar: a small always-on-top window shown whenever an app-control grant is live,
//! with Pause / Resume / Stop, plus the Ctrl+Alt+Esc hotkey that stops at once.
//!
//! It shares `computer-control.json` with the Python computer server (`smara.computer_control`): Stop writes
//! `stop_at` (every grant made before it is revoked), Pause sets `paused`. The server re-reads the file before every
//! step and during pointer travel and typing. Writes are atomic (temp file + rename). Nothing here holds a secret.
use serde::Serialize;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};

pub const LABEL: &str = "control-bar";
pub const HOTKEY: &str = "Ctrl+Alt+Esc";
static HOTKEY_OK: AtomicBool = AtomicBool::new(false);
static FOLDER: OnceLock<PathBuf> = OnceLock::new();

#[derive(Serialize, Debug, Clone, PartialEq)]
pub struct BarState {
    pub active: bool,
    pub paused: bool,
    pub until: f64,
    pub app: String,
    pub title: String,
    pub action: String,
    pub last_ts: f64,
    /// The agent holding the mouse and keyboard now (the input lease), else the one that acted last.
    pub agent: String,
    pub hotkey: String,
    pub hotkey_ok: bool,
}

fn now() -> f64 { SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0) }

fn folder() -> PathBuf { FOLDER.get().cloned().unwrap_or_default() }

fn read_json(path: &Path) -> Option<Option<Value>> {
    // None: no file. Some(None): unreadable. Some(Some(v)): an object.
    let text = match fs::read_to_string(path) { Ok(text) => text, Err(error) if error.kind() == std::io::ErrorKind::NotFound => return None, Err(_) => return Some(None) };
    Some(serde_json::from_str::<Value>(&text).ok().filter(Value::is_object))
}

/// (paused, stop_at). A damaged file counts as paused, the same as the Python side.
pub fn control(dir: &Path) -> (bool, f64) {
    match read_json(&dir.join("computer-control.json")) {
        None => (false, 0.0),
        Some(None) => (true, 0.0),
        Some(Some(value)) => (value["paused"].as_bool().unwrap_or(false), value["stop_at"].as_f64().unwrap_or(0.0)),
    }
}

pub fn state_in(dir: &Path, at: f64) -> BarState {
    let (paused, stop_at) = control(dir);
    let mut until: f64 = 0.0;
    if let Ok(entries) = fs::read_dir(dir.join("computer-grants")) {
        for entry in entries.flatten() {
            if entry.path().extension().and_then(|e| e.to_str()) != Some("json") { continue; }
            if let Some(Some(grant)) = read_json(&entry.path()) {
                let live = grant["enabled"].as_bool() == Some(true)
                    && grant["expires"].as_f64().unwrap_or(0.0) > at
                    && grant["created"].as_f64().unwrap_or(0.0) >= stop_at;
                if live { until = until.max(grant["expires"].as_f64().unwrap_or(0.0)); }
            }
        }
    }
    let last = read_json(&dir.join("computer-activity.json")).flatten().unwrap_or(Value::Null);
    let text = |key: &str| last[key].as_str().unwrap_or("").chars().take(80).collect::<String>();
    let input = read_json(&dir.join("computer-input.json")).flatten().unwrap_or(Value::Null);
    let holder = if input["until"].as_f64().unwrap_or(0.0) > at { input["label"].as_str().unwrap_or("").chars().take(80).collect::<String>() } else { String::new() };
    let agent = if holder.is_empty() { text("agent") } else { holder };
    BarState { active: until > 0.0, paused, until, app: text("app"), title: text("title"), action: text("action"),
        last_ts: last["ts"].as_f64().unwrap_or(0.0), agent, hotkey: HOTKEY.into(), hotkey_ok: HOTKEY_OK.load(Ordering::Relaxed) }
}

/// Pause/resume (Some) or stop (stop = true). Atomic replace so a reader never sees half a file.
pub fn write_control(dir: &Path, paused: Option<bool>, stop: bool, by: &str) -> Result<(), String> {
    let (current_paused, current_stop) = control(dir);
    let at = now();
    let value = json!({
        "paused": if stop { false } else { paused.unwrap_or(current_paused) },
        "stop_at": if stop { at } else { current_stop },
        "by": by, "at": at,
    });
    fs::create_dir_all(dir).map_err(|_| "Cannot reach the Smara data folder")?;
    let temporary = dir.join(format!("computer-control.{}.tmp", std::process::id()));
    fs::write(&temporary, value.to_string()).map_err(|_| "Cannot write the control file")?;
    fs::rename(&temporary, dir.join("computer-control.json")).map_err(|_| { let _ = fs::remove_file(&temporary); "Cannot write the control file".to_string() })
}

#[tauri::command]
pub fn computer_bar_state() -> BarState { state_in(&folder(), now()) }

#[tauri::command]
pub fn computer_bar_pause(paused: bool) -> Result<BarState, String> {
    write_control(&folder(), Some(paused), false, "bar")?;
    Ok(computer_bar_state())
}

#[tauri::command]
pub fn computer_bar_stop() -> Result<BarState, String> {
    write_control(&folder(), None, true, "bar")?;
    Ok(computer_bar_state())
}

fn show_bar(app: &AppHandle) {
    if app.get_webview_window(LABEL).is_some() { return; }
    let (width, height) = (560.0, 46.0);
    let x = app.primary_monitor().ok().flatten()
        .map(|m| (m.size().width as f64 / m.scale_factor() - width) / 2.0).unwrap_or(400.0);
    let _ = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("index.html#control-bar".into()))
        .title("Smara is controlling your PC")
        .inner_size(width, height).position(x.max(0.0), 6.0)
        .decorations(false).resizable(false).always_on_top(true).skip_taskbar(true).focused(false).shadow(true)
        .build();
}

fn hide_bar(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(LABEL) { let _ = window.close(); }
}

/// Start the watcher (shows/hides the bar) and the Stop hotkey. `dir` is the Smara data folder.
pub fn start(app: AppHandle, dir: PathBuf) {
    let _ = FOLDER.set(dir);
    start_hotkey();
    std::thread::spawn(move || {
        let mut shown = false;
        loop {
            let active = computer_bar_state().active;
            if active != shown {
                shown = active;
                let handle = app.clone();
                let _ = app.run_on_main_thread(move || if active { show_bar(&handle) } else { hide_bar(&handle) });
            }
            std::thread::sleep(Duration::from_millis(400));
        }
    });
}

#[cfg(windows)]
fn start_hotkey() {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{RegisterHotKey, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_ESCAPE};
    use windows_sys::Win32::UI::WindowsAndMessaging::{GetMessageW, MSG, WM_HOTKEY};
    std::thread::spawn(|| unsafe {
        // Registered on this thread with no window: WM_HOTKEY arrives in this thread's queue.
        if RegisterHotKey(std::ptr::null_mut(), 0x5301, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_ESCAPE as u32) == 0 { return; }
        HOTKEY_OK.store(true, Ordering::Relaxed);
        let mut message: MSG = std::mem::zeroed();
        while GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) > 0 {
            if message.message == WM_HOTKEY { let _ = write_control(&folder(), None, true, "hotkey"); }
        }
    });
}

#[cfg(not(windows))]
fn start_hotkey() {}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("smara-bar-{}-{}", std::process::id(), now().to_bits()));
        fs::create_dir_all(dir.join("computer-grants")).unwrap();
        dir
    }

    #[test]
    fn bar_shows_only_for_a_live_grant_and_stop_revokes_it() {
        let dir = temp();
        let at = now();
        assert!(!state_in(&dir, at).active);
        fs::write(dir.join("computer-grants/a.json"), json!({"enabled": true, "created": at - 5.0, "expires": at + 600.0}).to_string()).unwrap();
        fs::write(dir.join("computer-grants/b.json"), json!({"enabled": false}).to_string()).unwrap();
        let state = state_in(&dir, at);
        assert!(state.active && !state.paused && (state.until - (at + 600.0)).abs() < 1.0);
        assert!(!state_in(&dir, at + 601.0).active);                       // expired
        write_control(&dir, None, true, "test").unwrap();
        assert!(!state_in(&dir, now()).active);                            // created before Stop
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn pause_resume_keep_stop_time_and_a_damaged_file_means_paused() {
        let dir = temp();
        write_control(&dir, None, true, "test").unwrap();
        let (_, stop_at) = control(&dir);
        write_control(&dir, Some(true), false, "test").unwrap();
        assert_eq!(control(&dir), (true, stop_at));
        write_control(&dir, Some(false), false, "test").unwrap();
        assert_eq!(control(&dir), (false, stop_at));
        fs::write(dir.join("computer-control.json"), "{oops").unwrap();
        assert!(control(&dir).0);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn last_action_is_shown_and_trimmed() {
        let dir = temp();
        fs::write(dir.join("computer-activity.json"), json!({"ts": 5.0, "app": "excel.exe", "title": "x".repeat(200), "action": "type_text"}).to_string()).unwrap();
        let state = state_in(&dir, now());
        assert_eq!((state.app.as_str(), state.action.as_str(), state.title.len()), ("excel.exe", "type_text", 80));
        let at = now();
        fs::write(dir.join("computer-input.json"), json!({"label": "Kiran: day book", "until": at + 10.0}).to_string()).unwrap();
        assert_eq!(state_in(&dir, at).agent, "Kiran: day book");
        assert_eq!(state_in(&dir, at + 11.0).agent, "");                 // lease over, no agent in the last action
        fs::remove_dir_all(dir).unwrap();
    }
}
