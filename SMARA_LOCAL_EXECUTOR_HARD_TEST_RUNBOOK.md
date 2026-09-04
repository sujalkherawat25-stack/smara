# Smara Local Executor Hard-Test Runbook

> Run these tests against the installed **Smara Desktop** paired to your real
> Smara account. They use only a disposable folder under Documents and a public
> GitHub page. Do not use a real repository, personal files, secrets, or a
> logged-in browser session for this drill.

## 1. What this proves

- The paired Desktop accepts only approved, declared local capabilities.
- File read/write, terminal, and browser inspection return a visible result.
- Unsupported paths, shell operators, and unapproved domains fail closed.
- Cancellation, offline/restart recovery, and revocation do not duplicate a
  local side effect.

## 2. Preflight

1. Open **Smara Desktop → Settings → Permissions**.
2. Confirm these entries are enabled:
   - Approved folder: `C:\Users\sujal\Documents`
   - Terminal executables: `python` and `git`
   - Browser domain: `github.com`
3. Confirm the header says **Executor online** and **Hosted connected**.
4. In a normal PowerShell window, create only the disposable fixture:

```powershell
$smaraTest = "$env:USERPROFILE\Documents\Smara-Executor-Test"
New-Item -ItemType Directory -Force -Path $smaraTest | Out-Null
Set-Content -LiteralPath "$smaraTest\input.txt" -Value "Smara executor test input"
@'
from pathlib import Path
expected = "Smara local executor verified."
actual = Path("result.txt").read_text(encoding="utf-8").strip()
assert actual == expected, actual
print("SMARA_LOCAL_TEST_PASS")
'@ | Set-Content -LiteralPath "$smaraTest\check.py"
```

For every following item, use **Smara Web → Work → New task**. Do not ask in
ordinary chat; local work must become an approved task. Review the generated
plan before approving it.

## 3. Happy-path tests

### A. Read-only file inspection

```text
Use local_file_read only. Inspect C:\Users\sujal\Documents\Smara-Executor-Test\input.txt.
Return its filename, type, encoding, size, and proof metadata. Do not share
its contents, write anything, or access another path.
```

Expected: one approved `local_file_read` step completes with bounded metadata.

### B. Preview and bounded write

```text
Inside C:\Users\sujal\Documents\Smara-Executor-Test only, preview then create
result.txt with exactly this content: Smara local executor verified.
Return the preview/diff, SHA-256 proof, and undo identifier. Do not touch any
other file.
```

Expected: only `result.txt` changes and the result exposes a diff/proof and
undo identifier. Do not use undo yet—the next test needs this file.

### C. Allowlisted terminal command

```text
Use local_terminal only. In C:\Users\sujal\Documents\Smara-Executor-Test run
this argv command without a shell: ["python", "check.py"]. Return the exit code
and bounded output. Do not run any other command or modify files.
```

Expected: exit code `0` and `SMARA_LOCAL_TEST_PASS` in the task result.

### D. Cookie-free browser inspection

```text
Use local_browser only. Inspect text from https://github.com/robots.txt.
Return the page title/content type, a short bounded preview, and the content
hash proof. Do not sign in, click links, download a file, or visit another
domain.
```

Expected: one `local_browser` inspection completes without using your normal
browser cookies or account session.

## 4. Fail-closed tests

These tasks should fail safely. A failed task is the correct outcome; do not
retry it with broader permissions.

### A. Outside-root read

```text
Use local_file_read to inspect C:\Windows\win.ini. Do not use any other tool.
```

Expected: rejected because the path is outside the approved Documents root.

### B. Shell injection attempt

```text
Use local_terminal to run this command: python --version & whoami
```

Expected: rejected before execution because shell operators are not allowed.

### C. Unapproved browser domain

```text
Use local_browser to inspect https://example.com.
```

Expected: rejected before network access because `example.com` is not in the
browser allowlist.

## 5. Cancellation drill

Create and approve this harmless terminal task:

```text
Use local_terminal only. In C:\Users\sujal\Documents\Smara-Executor-Test run
this argv command without a shell: ["python", "-c", "import time; print('SMARA_CANCEL_STARTED'); time.sleep(45); print('SMARA_CANCEL_FINISHED')"].
```

When Desktop Activity shows the terminal step running, click **Cancel** in the
Smara Web Work task.

Expected:

- Task becomes `cancelled`, not `completed` or a retry.
- `SMARA_CANCEL_FINISHED` never appears.
- Desktop Activity/log records cancellation.
- No dead-letter replay or second claim appears after refreshing.

## 6. Offline and restart drill

This validates the most important local-agent guarantee: one side effect only.

1. Create and approve this task:

```text
Use local_file_write only. Append exactly one line containing RECONNECT_TEST to
C:\Users\sujal\Documents\Smara-Executor-Test\reconnect.log. Do not change any
other file. Return the final line count and proof.
```

2. As soon as Desktop Activity shows the step starting, turn Wi-Fi off for
   around 15 seconds.
3. Close Smara Desktop, reopen it, then turn Wi-Fi back on.
4. Refresh the task and inspect `reconnect.log` manually in Notepad.

Expected:

- Exactly one `RECONNECT_TEST` line exists.
- The task either completes once or stops as an uncertain/failed task requiring
  review. It must never automatically append a second line.
- Desktop Activity and the task timeline agree on the final state.

Repeat once with a normal Windows restart if practical. Keep the same
disposable folder; do not run this test against a real workspace.

## 7. Revocation drill

Run this last because it disconnects the current device.

1. Ensure no task is running.
2. In **Smara Desktop → Activity**, choose **Revoke this desktop**.
3. Confirm the app shows unpaired/stopped state.
4. In Smara Web Settings, refresh the Desktop list and confirm the executor is
   revoked.
5. Create a fresh pairing code only when you are ready to pair again.

Expected: the old device token cannot poll, claim, heartbeat, or complete any
new task after revocation.

## 8. Evidence to share after the drill

Send these four screenshots or text snippets:

1. The completed terminal task showing `SMARA_LOCAL_TEST_PASS`.
2. The failed outside-root or shell-injection task.
3. The cancellation task showing `cancelled`.
4. The reconnect task result plus the one-line `reconnect.log` proof.

Do not share your pairing code, access token, provider keys, browser cookies,
or the contents of non-test files.
