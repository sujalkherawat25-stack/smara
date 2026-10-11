# Smara v0.1.12 — Changes review and safe Undo

- Review unstaged/staged changes by file or hunk; stage, unstage and revert
  reviewed patches with confirmation.
- Compare committed branch changes locally without fetching or modifying them.
- Review and Undo supported last-turn file changes with local recovery copies.
  Pre-existing and non-conflicting later edits are kept; stale reviews and
  conflicting changes are refused. Undo leaves chat history and the index alone.
- Review writes are blocked while native turns or workers are pending/active.
- Agent terminal displays native command output, working folder, duration and
  exit codes. It is a transcript, not an additional interactive shell.
- New model-free `smara changes` CLI commands and Help instructions.
- Fixed concurrent-preview locking and unconfigured-model startup status.
- CLI version output distinguishes the Smara product release from the imported
  native core's independent development version.

Tests cover real temporary Git repositories, real pytest coding outcomes,
synthetic protocol/component events and isolated Windows visual checks.
See [the workflow guide and verification limits](https://github.com/sujalkherawat25-stack/smara/blob/v0.1.12/native/CHANGES.md).
This release is not a claim of full Codex GUI parity or a new live-model evaluation.

Installation preserves preferences, protected keys and history. Binary/large,
filtered and unsupported files are explicitly excluded from review/Undo; snapshots
and recovery copies are private local data. No new message-length limits were added.
