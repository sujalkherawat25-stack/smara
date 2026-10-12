//! Native-only Desktop shell. The Rust harness owns all agent/tool execution.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use base64::Engine as _;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
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
        output.take(8_000_001).read_to_end(&mut bytes).map(|_| bytes)
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
    if bytes.len() > 8_000_000 { return Err("Settings response exceeds safety limit".into()); }
    let reply: Value = serde_json::from_slice(&bytes).map_err(|_| "Invalid settings response")?;
    if let Some(error) = reply.get("error").and_then(Value::as_str) { return Err(error.to_owned()); }
    reply.get("result").cloned().ok_or("Missing settings response".into())
}
#[tauri::command]
async fn native_manage(request: Value) -> Result<Value, String> {
    if !matches!(request.get("operation").and_then(Value::as_str), Some("bootstrap" | "goals" | "voice" | "add_project" | "select_project" | "save_project_options" | "select_model" | "select_search" | "save_credential" | "delete_credential" | "save_model" | "test_model" | "test_search")) {
        return Err("Unsupported UI settings operation; secret reads and legacy execution are unavailable".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        if request["operation"] == "bootstrap" || request["operation"] == "goals" || request["operation"] == "voice" { management(request) }
        else { native_runtime::with_disconnected(|| management(request)) }
    }).await.map_err(|_| "Settings worker failed".to_owned())?
}
/// Local, user-directed Git operations, not a model tool or shell escape.
#[tauri::command]
async fn native_review(request: Value) -> Result<Value, String> {
    let operation = request.get("operation").and_then(Value::as_str).unwrap_or_default();
    if !matches!(operation, "status" | "file" | "capture" | "seal" | "apply" | "recover") {
        return Err("Unsupported Git review operation".into());
    }
    let read_only = matches!(operation, "status" | "file");
    tauri::async_runtime::spawn_blocking(move || {
        let action = || management(json!({"operation": "review", "review": request}));
        if read_only { action() } else { native_runtime::with_review_idle(action) }
    }).await.map_err(|_| "Git review worker failed".to_owned())?
}
struct Connection { allowed_roots: Vec<String>, active_workspace: String }
fn current_connection() -> Result<Connection, String> {
    let value = management(json!({"operation":"bootstrap"}))?;
    let preferences = value.get("preferences").ok_or("Project settings are unavailable")?;
    Ok(Connection {
        allowed_roots: preferences["projects"].as_array().ok_or("Projects are invalid")?
            .iter().filter_map(|p| p["workspace"].as_str().map(str::to_owned)).collect(),
        active_workspace: preferences["workspace"].as_str().ok_or("Choose a project first")?.to_owned(),
    })
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

const MAX_INLINE_TEXT_PREVIEW_BYTES: u64 = 1_000_000;
const MAX_INLINE_BINARY_PREVIEW_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Debug, Clone, Serialize)]
struct NativeFilePreview {
    file_name: String,
    relative_path: String,
    size_bytes: u64,
    extension: String,
    kind: String,
    content: Option<String>,
    data_url: Option<String>,
    note: Option<String>,
    can_open_externally: bool,
}

fn can_open_workspace_file_externally(extension: &str) -> bool {
    matches!(extension,
        "txt" | "md" | "markdown" | "pdf" | "png" | "jpg" | "jpeg" | "gif" | "webp"
        | "docx" | "xlsx" | "pptx" | "odt" | "ods" | "odp"
    )
}

fn active_preview_workspace(workspace: &str) -> Result<PathBuf, String> {
    let connection = current_connection()?;
    let active = selected_workspace_dir(&connection.active_workspace, &connection.allowed_roots)?
        .ok_or_else(|| "Choose a project before previewing files.".to_owned())?;
    let requested = selected_workspace_dir(workspace, &connection.allowed_roots)?
        .ok_or_else(|| "Choose a project before previewing files.".to_owned())?;
    if active != requested { return Err("Preview is limited to the currently selected project.".to_owned()); }
    Ok(active)
}

fn canonical_workspace_file(root: &Path, requested: &str) -> Result<(PathBuf, PathBuf), String> {
    if requested.trim().is_empty() { return Err("Choose a file to preview.".to_owned()); }
    let root = fs::canonicalize(root).map_err(|_| "The selected project folder is unavailable.".to_owned())?;
    let input = PathBuf::from(requested);
    let candidate = if input.is_absolute() { input } else { root.join(input) };
    let path = fs::canonicalize(candidate).map_err(|_| "The file is missing or inaccessible.".to_owned())?;
    if !path.starts_with(&root) { return Err("Preview is limited to files inside the selected project.".to_owned()); }
    if !fs::metadata(&path).map_err(|_| "The file is missing or inaccessible.".to_owned())?.is_file() {
        return Err("Only regular files can be previewed.".to_owned());
    }
    Ok((root, path))
}

fn read_preview_bytes(path: &Path, limit: u64) -> Result<Option<Vec<u8>>, String> {
    let mut bytes = Vec::new();
    fs::File::open(path).map_err(|_| "The file could not be read.".to_owned())?
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "The file could not be read.".to_owned())?;
    if bytes.len() as u64 > limit { Ok(None) } else { Ok(Some(bytes)) }
}

fn workspace_file_preview(root: &Path, requested: &str) -> Result<NativeFilePreview, String> {
    let (root, path) = canonical_workspace_file(root, requested)?;
    let size_bytes = fs::metadata(&path).map_err(|_| "The file is missing or inaccessible.".to_owned())?.len();
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("").to_ascii_lowercase();
    let can_open_externally = can_open_workspace_file_externally(&extension);
    let file_name = path.file_name().and_then(|value| value.to_str()).unwrap_or("File").to_owned();
    let relative_path = path.strip_prefix(&root).map_err(|_| "Preview is limited to files inside the selected project.".to_owned())?
        .to_string_lossy().replace('\\', "/");
    let text_extensions = ["txt", "md", "markdown", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml", "xml", "html", "htm", "css", "js", "jsx", "ts", "tsx", "mjs", "cjs", "py", "rs", "go", "java", "c", "h", "cpp", "hpp", "sh", "bash", "ps1", "bat", "sql", "log", "ini"];
    if text_extensions.contains(&extension.as_str()) {
        if size_bytes > MAX_INLINE_TEXT_PREVIEW_BYTES {
            return Ok(NativeFilePreview { file_name, relative_path, size_bytes, extension, kind: "too_large".to_owned(), content: None, data_url: None, note: Some("Text previews are limited to 1 MB.".to_owned()), can_open_externally });
        }
        let Some(bytes) = read_preview_bytes(&path, MAX_INLINE_TEXT_PREVIEW_BYTES)? else {
            return Ok(NativeFilePreview { file_name, relative_path, size_bytes, extension, kind: "too_large".to_owned(), content: None, data_url: None, note: Some("Text previews are limited to 1 MB.".to_owned()), can_open_externally });
        };
        return match String::from_utf8(bytes) {
            Ok(content) => Ok(NativeFilePreview { file_name, relative_path, size_bytes, extension, kind: "text".to_owned(), content: Some(content), data_url: None, note: None, can_open_externally }),
            Err(_) => Ok(NativeFilePreview { file_name, relative_path, size_bytes, extension, kind: "unsupported".to_owned(), content: None, data_url: None, note: Some("This file is not UTF-8 text and cannot be shown inline.".to_owned()), can_open_externally }),
        };
    }
    let (kind, mime) = match extension.as_str() {
        "pdf" => ("pdf", "application/pdf"),
        "png" => ("image", "image/png"),
        "jpg" | "jpeg" => ("image", "image/jpeg"),
        "gif" => ("image", "image/gif"),
        "webp" => ("image", "image/webp"),
        _ => return Ok(NativeFilePreview { file_name, relative_path, size_bytes, extension, kind: "unsupported".to_owned(), content: None, data_url: None, note: Some(if can_open_externally { "This format can be opened with its default viewer." } else { "This file type is not opened externally for safety." }.to_owned()), can_open_externally }),
    };
    if size_bytes > MAX_INLINE_BINARY_PREVIEW_BYTES {
        return Ok(NativeFilePreview { file_name, relative_path, size_bytes, extension, kind: "too_large".to_owned(), content: None, data_url: None, note: Some("PDF and image previews are limited to 8 MB.".to_owned()), can_open_externally });
    }
    let Some(bytes) = read_preview_bytes(&path, MAX_INLINE_BINARY_PREVIEW_BYTES)? else {
        return Ok(NativeFilePreview { file_name, relative_path, size_bytes, extension, kind: "too_large".to_owned(), content: None, data_url: None, note: Some("PDF and image previews are limited to 8 MB.".to_owned()), can_open_externally });
    };
    let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);
    Ok(NativeFilePreview { file_name, relative_path, size_bytes, extension, kind: kind.to_owned(), content: None, data_url: Some(format!("data:{mime};base64,{encoded}")), note: None, can_open_externally })
}

#[tauri::command]
async fn native_preview_file(workspace: String, path: String) -> Result<NativeFilePreview, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = active_preview_workspace(&workspace)?;
        workspace_file_preview(&root, &path)
    }).await.map_err(|_| "File preview worker failed".to_owned())?
}

mod native_context;

#[tauri::command]
async fn native_project_files(workspace: String, path: String) -> Result<native_context::Listing, String> {
    tauri::async_runtime::spawn_blocking(move || {
        native_context::list(&active_preview_workspace(&workspace)?, &path)
    }).await.map_err(|_| "Project file listing failed".to_owned())?
}

#[tauri::command]
async fn native_open_workspace_file(workspace: String, path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = active_preview_workspace(&workspace)?;
        let (_, resolved) = canonical_workspace_file(&root, &path)?;
        let extension = resolved.extension().and_then(|value| value.to_str()).unwrap_or("").to_ascii_lowercase();
        if !can_open_workspace_file_externally(&extension) {
            return Err("This file type is not opened externally for safety.".to_owned());
        }
        open::that_detached(&resolved).map_err(|_| "Could not open the file in its default app.".to_owned())
    }).await.map_err(|_| "File opener worker failed".to_owned())?
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
        .invoke_handler(tauri::generate_handler![native_status, native_start, native_send, native_stop, native_manage, native_review, native_open_url, native_preview_file, native_open_workspace_file, native_project_files])
        .build(tauri::generate_context!()).expect("Could not build Smara Desktop")
        .run(|_, event| {
            if matches!(event, tauri::RunEvent::ExitRequested { .. }) { let _ = native_runtime::stop_current(); }
        });
}

#[cfg(test)]
mod artifact_preview_tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    struct TempWorkspace(PathBuf);

    impl TempWorkspace {
        fn new() -> Self {
            let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
            let base = std::env::temp_dir().join(format!("smara-preview-{}-{nonce}", std::process::id()));
            fs::create_dir_all(base.join("project")).unwrap();
            Self(base)
        }

        fn root(&self) -> PathBuf { self.0.join("project") }
    }

    impl Drop for TempWorkspace {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    #[test]
    fn previews_utf8_text_and_reports_project_relative_path() {
        let temp = TempWorkspace::new();
        fs::create_dir_all(temp.root().join("reports")).unwrap();
        fs::write(temp.root().join("reports/summary.md"), "Verified summary").unwrap();

        let preview = workspace_file_preview(&temp.root(), "reports/summary.md").unwrap();

        assert_eq!(preview.kind, "text");
        assert_eq!(preview.relative_path, "reports/summary.md");
        assert_eq!(preview.content.as_deref(), Some("Verified summary"));
        assert!(preview.data_url.is_none());
    }

    #[test]
    fn refuses_paths_outside_the_selected_project() {
        let temp = TempWorkspace::new();
        fs::write(temp.0.join("outside.txt"), "private").unwrap();

        assert!(workspace_file_preview(&temp.root(), "../outside.txt").is_err());
        assert!(workspace_file_preview(&temp.root(), temp.0.join("outside.txt").to_str().unwrap()).is_err());
    }

    #[test]
    fn enforces_preview_size_caps_and_encodes_supported_images() {
        let temp = TempWorkspace::new();
        fs::write(temp.root().join("large.txt"), vec![b'x'; MAX_INLINE_TEXT_PREVIEW_BYTES as usize + 1]).unwrap();
        fs::write(temp.root().join("pixel.png"), [137, 80, 78, 71]).unwrap();

        let large = workspace_file_preview(&temp.root(), "large.txt").unwrap();
        assert_eq!(large.kind, "too_large");
        assert!(large.content.is_none());

        let image = workspace_file_preview(&temp.root(), "pixel.png").unwrap();
        assert_eq!(image.kind, "image");
        assert_eq!(image.data_url.as_deref(), Some("data:image/png;base64,iVBORw=="));
        assert!(image.can_open_externally);
    }

    #[test]
    fn external_open_is_allowlisted_and_never_launches_scripts_or_binaries() {
        assert!(can_open_workspace_file_externally("pdf"));
        assert!(can_open_workspace_file_externally("docx"));
        for extension in ["exe", "com", "bat", "cmd", "ps1", "vbs", "js", "html", "svg", "msi", "lnk"] {
            assert!(!can_open_workspace_file_externally(extension), "unexpected external open for {extension}");
        }
    }
}
