//! Opt-in coding checkouts. Native policy remains the execution authority.
//! No uncommitted-file copying, automatic merge, or destructive cleanup.

use crate::config::Config;
use codex_protocol::intersect_effective_permission_profiles;
use codex_protocol::models::PermissionProfile;
use codex_protocol::models::SandboxEnforcement;
use codex_protocol::permissions::FileSystemSandboxPolicy;
use codex_protocol::permissions::FileSystemSandboxPolicyContext;
use codex_protocol::permissions::NetworkSandboxPolicy;
use codex_protocol::protocol::SandboxPolicy;
use codex_utils_absolute_path::AbsolutePathBuf;
use codex_utils_path_uri::PathUri;
use codex_worktree::{CreateWorktree, WorktreeManager, WorktreeSettings};
use std::path::{Path, PathBuf};

const WORKER_DIRECTORY: &str = ".smara/worker-worktrees";

fn manager(config: &Config) -> Result<WorktreeManager, String> {
    let cwd = dunce::canonicalize(config.cwd.as_path()).map_err(|e| e.to_string())?;
    let root = cwd.join(WORKER_DIRECTORY);
    // Reject redirects before creating directories, including a junction in
    // .smara. Existing descendants are also checked on restore by Git's list.
    for path in [cwd.join(".smara"), root.clone()] {
        if path.exists()
            && !dunce::canonicalize(&path)
                .map_err(|e| e.to_string())?
                .starts_with(&cwd)
        {
            return Err("Worker storage escapes the parent workspace".into());
        }
    }
    let uri = PathUri::from_abs_path(&config.cwd);
    let context = FileSystemSandboxPolicyContext {
        cwd: &uri,
        workspace_roots: std::slice::from_ref(&uri),
        user_home_dir: None,
        temporary_directories: Some(&[]),
    };
    if !config
        .permissions
        .file_system_sandbox_policy()
        .can_write_path(
            &PathUri::from_host_native_path(&root).map_err(|e| e.to_string())?,
            &context,
        )
    {
        return Err("Parent permissions do not allow worker storage".into());
    }
    Ok(WorktreeManager::new(WorktreeSettings {
        root,
        auto_cleanup_enabled: false,
        keep_count: 15,
    }))
}

pub(crate) fn narrow(config: &mut Config, cwd: &Path) -> Result<(), String> {
    let authority = config.permissions.effective_permission_profile();
    let cwd = AbsolutePathBuf::from_absolute_path(cwd).map_err(|e| e.to_string())?;
    let sandbox = SandboxPolicy::WorkspaceWrite {
        writable_roots: Vec::new(),
        network_access: false,
        exclude_tmpdir_env_var: true,
        exclude_slash_tmp: true,
    };
    let file_system =
        FileSystemSandboxPolicy::from_legacy_sandbox_policy_for_cwd(&sandbox, cwd.as_path());
    let requested = PermissionProfile::from_runtime_permissions_with_enforcement(
        SandboxEnforcement::from_legacy_sandbox_policy(&sandbox),
        &file_system,
        NetworkSandboxPolicy::from(&sandbox),
    )
    .materialize_project_roots_with_workspace_roots(std::slice::from_ref(&cwd));
    let bounded = intersect_effective_permission_profiles(&authority, &requested, cwd.as_path())
        .map_err(|e| format!("Worker permissions cannot be narrowed safely: {e}"))?;
    config
        .permissions
        .set_permission_profile(bounded)
        .map_err(|e| e.to_string())?;
    config.permissions.set_workspace_roots(vec![cwd.clone()]);
    config.workspace_roots = vec![cwd.clone()];
    config.workspace_roots_explicit = true;
    config.cwd = cwd;
    Ok(())
}

pub(crate) async fn create(config: &mut Config) -> Result<PathBuf, String> {
    if std::env::var("SMARA_NATIVE_WORKTREE_WORKERS").as_deref() != Ok("1") {
        return Err("Isolated workers are disabled; opt in with --smara-workers or Desktop before connecting".into());
    }
    if !matches!(
        config.legacy_sandbox_policy(),
        SandboxPolicy::WorkspaceWrite { .. }
    ) {
        return Err("Creating a coding checkout requires a workspace-write parent".into());
    }
    let manager = manager(config)?;
    let source_cwd = config.cwd.to_path_buf();
    if codex_worktree::repository_root(&source_cwd).map_err(|e| e.to_string())?
        != dunce::canonicalize(&source_cwd).map_err(|e| e.to_string())?
    {
        return Err("Select the repository root for isolated workers".into());
    }
    let checkout = tokio::task::spawn_blocking(move || {
        manager.create(&CreateWorktree {
            source_cwd,
            base: Some("HEAD".into()),
        })
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())?;
    narrow(config, &checkout.cwd)?;
    Ok(checkout.cwd)
}

/// A cold reload must never replace a worker checkout with the parent's cwd.
pub(crate) fn restore(config: &mut Config, stored_cwd: &Path) -> Result<bool, String> {
    let stored_cwd = dunce::canonicalize(stored_cwd)
        .map_err(|_| "Child working directory is missing; no replacement or replay".to_string())?;
    if stored_cwd == dunce::canonicalize(config.cwd.as_path()).map_err(|e| e.to_string())? {
        return Ok(false);
    }
    let manager = manager(config)?;
    let storage = dunce::canonicalize(&manager.settings().root)
        .unwrap_or_else(|_| manager.settings().root.clone());
    if !stored_cwd.starts_with(&storage) {
        return Err(
            "Child working directory differs from its parent; refusing to widen or relocate it"
                .into(),
        );
    }
    let checkouts = manager
        .list(config.cwd.as_path())
        .map_err(|e| e.to_string())?;
    let checkout = checkouts
        .into_iter()
        .find(|checkout| dunce::canonicalize(&checkout.cwd).ok().as_ref() == Some(&stored_cwd))
        .ok_or_else(|| {
            "Worker checkout is missing or unregistered; no replacement or replay".to_string()
        })?;
    narrow(config, &checkout.cwd)?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use codex_protocol::protocol::AskForApproval;

    fn writable(config: &Config, path: &Path) -> bool {
        let cwd = PathUri::from_abs_path(&config.cwd);
        let context = FileSystemSandboxPolicyContext {
            cwd: &cwd,
            workspace_roots: std::slice::from_ref(&cwd),
            user_home_dir: None,
            temporary_directories: Some(&[]),
        };
        config
            .permissions
            .file_system_sandbox_policy()
            .can_write_path(&PathUri::from_host_native_path(path).unwrap(), &context)
    }

    #[tokio::test]
    async fn narrowing_keeps_approval_but_denies_parent_and_network() {
        let temp = tempfile::tempdir().unwrap();
        let mut config = crate::config::test_config().await;
        config.cwd = AbsolutePathBuf::from_absolute_path(temp.path()).unwrap();
        config
            .permissions
            .approval_policy
            .set(AskForApproval::OnRequest)
            .unwrap();
        config
            .set_legacy_sandbox_policy(SandboxPolicy::WorkspaceWrite {
                writable_roots: Vec::new(),
                network_access: true,
                exclude_tmpdir_env_var: false,
                exclude_slash_tmp: false,
            })
            .unwrap();
        let checkout = temp.path().join(".smara/worker-worktrees/test/project");
        std::fs::create_dir_all(&checkout).unwrap();
        narrow(&mut config, &checkout).unwrap();
        assert!(writable(&config, &checkout.join("implementation.py")));
        assert!(!writable(&config, &temp.path().join("implementation.py")));
        assert!(!writable(&config, &checkout.join(".git/config")));
        assert!(!config.permissions.network_sandbox_policy().is_enabled());
        assert_eq!(
            config.permissions.approval_policy.value(),
            AskForApproval::OnRequest
        );
    }

    #[tokio::test]
    async fn cold_restore_cannot_relocate_to_an_unrelated_project() {
        let temp = tempfile::tempdir().unwrap();
        let mut config = crate::config::test_config().await;
        config.cwd = AbsolutePathBuf::from_absolute_path(temp.path()).unwrap();
        config
            .set_legacy_sandbox_policy(SandboxPolicy::WorkspaceWrite {
                writable_roots: Vec::new(),
                network_access: false,
                exclude_tmpdir_env_var: true,
                exclude_slash_tmp: true,
            })
            .unwrap();
        assert!(!restore(&mut config, temp.path()).unwrap());
        let unrelated = temp.path().join("unrelated");
        std::fs::create_dir(&unrelated).unwrap();
        assert!(
            restore(&mut config, &unrelated)
                .unwrap_err()
                .contains("refusing")
        );
    }

    #[tokio::test]
    async fn cold_restore_accepts_registered_canonical_and_verbatim_paths() {
        let temp = tempfile::tempdir().unwrap();
        for args in [
            vec!["init"],
            vec![
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "fixture",
            ],
        ] {
            let output = std::process::Command::new("git")
                .args(args)
                .current_dir(temp.path())
                .output()
                .unwrap();
            assert!(output.status.success());
        }
        let mut config = crate::config::test_config().await;
        config.cwd = AbsolutePathBuf::from_absolute_path(temp.path()).unwrap();
        config
            .set_legacy_sandbox_policy(SandboxPolicy::WorkspaceWrite {
                writable_roots: Vec::new(),
                network_access: false,
                exclude_tmpdir_env_var: true,
                exclude_slash_tmp: true,
            })
            .unwrap();
        let checkout = manager(&config)
            .unwrap()
            .create(&CreateWorktree {
                source_cwd: temp.path().to_path_buf(),
                base: Some("HEAD".into()),
            })
            .unwrap();
        // std canonicalize returns a verbatim \\?\ path on Windows. Persisted
        // SQLite paths use that form even when RPC renders a normal path.
        let persisted = std::fs::canonicalize(&checkout.cwd).unwrap();
        assert!(restore(&mut config, &persisted).unwrap());
        assert_eq!(
            dunce::canonicalize(config.cwd.as_path()).unwrap(),
            dunce::canonicalize(checkout.cwd).unwrap()
        );
    }
}
