use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Serialize)]
pub struct Entry { pub name: String, pub path: String, pub directory: bool, pub size: u64 }

#[derive(Serialize)]
pub struct Listing { pub path: String, pub entries: Vec<Entry>, pub truncated: bool }

fn relative(root: &Path, path: &Path) -> String {
    path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/")
}

/// Metadata only. This does not read file contents or send anything to a model.
pub fn list(root: &Path, requested: &str) -> Result<Listing, String> {
    let root = fs::canonicalize(root).map_err(|_| "Project folder is unavailable.")?;
    let input = PathBuf::from(requested);
    let path = fs::canonicalize(if input.is_absolute() { input } else { root.join(input) })
        .map_err(|_| "Folder or file is missing or inaccessible.")?;
    if !path.starts_with(&root) { return Err("Choose files inside the selected project.".into()); }
    let metadata = fs::metadata(&path).map_err(|_| "Folder or file is inaccessible.")?;
    let entry = |path: &Path, metadata: fs::Metadata| Entry {
        name: path.file_name().unwrap_or_default().to_string_lossy().into(),
        path: relative(&root, path), directory: metadata.is_dir(), size: metadata.len(),
    };
    if metadata.is_file() {
        return Ok(Listing { path: relative(&root, path.parent().unwrap()), entries: vec![entry(&path, metadata)], truncated: false });
    }
    if !metadata.is_dir() { return Err("Choose a regular project file or folder.".into()); }
    let mut entries = Vec::new();
    let mut truncated = false;
    for item in fs::read_dir(&path).map_err(|_| "Folder cannot be listed.")? {
        let Ok(item) = item else { continue };
        if matches!(item.file_name().to_str(), Some(".git" | ".venv" | "node_modules" | "__pycache__")) { continue; }
        let Ok(resolved) = fs::canonicalize(item.path()) else { continue };
        if !resolved.starts_with(&root) { continue; }
        let Ok(metadata) = fs::metadata(&resolved) else { continue };
        if !metadata.is_dir() && !metadata.is_file() { continue; }
        if entries.len() == 1000 { truncated = true; break; }
        entries.push(entry(&resolved, metadata));
    }
    entries.sort_by(|a, b| b.directory.cmp(&a.directory).then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase())));
    Ok(Listing { path: relative(&root, &path), entries, truncated })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn metadata_browser_confines_paths_and_does_not_recurse_into_dependencies() {
        let base = std::env::temp_dir().join(format!("smara-context-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        let root = base.join("project");
        fs::create_dir_all(root.join(".git")).unwrap();
        fs::create_dir_all(root.join("reports")).unwrap();
        fs::write(root.join("reports/चयन.txt"), "local context").unwrap();
        fs::write(base.join("outside.txt"), "private").unwrap();
        let top = list(&root, "").unwrap();
        assert_eq!(top.entries.len(), 1);
        assert!(top.entries[0].directory);
        let file = list(&root, "reports/चयन.txt").unwrap();
        assert_eq!(file.entries[0].path, "reports/चयन.txt");
        assert_eq!(file.entries[0].size, 13);
        assert!(list(&root, "../outside.txt").is_err());
        assert!(list(&root, base.to_str().unwrap()).is_err());
        fs::remove_dir_all(base).unwrap();
    }
}
