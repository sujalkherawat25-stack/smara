# Smara and the team: how the desktop agents work

## Shape: one manager, named employees, background jobs

```
Owner
  └─ Smara (manager, renamable)  ← default chat, first point of contact
       ├─ answers quick questions itself
       ├─ hires / changes employees and saves standing rules   (team_hire, team_update, team_remember: owner confirms each)
       ├─ hands jobs to employees                               (team_delegate → that employee's background task)
       └─ reports what the team is doing                        (team_list, team_tasks)
  └─ Employees (Kiran · Accounts, Meera · Mail handler, ...)
       ├─ each has a job description + standing rules (saved with the roster)
       ├─ own chats the owner can open directly (sidebar)
       └─ background tasks on the task board (one runtime session each, budgets, approvals)
```

The session / sub-session idea is kept simple. A chat is a session. A job Smara hands out is a sub-session: a background task
that runs as that employee, with its own budget. It shows on the task board, and outward steps inside it come to the
owner's approval card. No agent talks to another agent directly. Smara reads the team's state through `team_list` and
`team_tasks`, and the owner is always the one who approves.

## Why this shape

| Option | What it is | Verdict |
|---|---|---|
| One assistant with many skills | One chat, everything in its prompt | Simple, but no "employees": jobs, rules and work are not separated, and the prompt grows. |
| Peer agents talking to each other | Agents message each other freely | Hard to follow and to stop; prompt injection can spread from one agent to another. Not used. |
| **Manager + employees + background jobs** (chosen) | One router (Smara), named workers with jobs, work in bounded background tasks | Matches how a shop owner thinks ("tell Smara, Smara gives it to Kiran"). Every change to the team and every outward step goes through the owner. Reuses the M3 task board and leases. |

## Safety

- **Hiring and changing an employee always ask the owner.** That covers `team_hire`, `team_update` and `team_remember`,
  each shown as an approval card ("Hire Meera as Mail handler?", with the job text). A web page or file cannot quietly
  rewrite an employee.
- **Background jobs.** `team_delegate` and the read tools need no prompt. Delegated jobs are read-only background
  tasks, capped at 6 cycles and 24 requests, and their outward steps still ask the owner.
- **The chat stays usable.** Only a task that edits project files blocks sending; employees reading and drafting do
  not.

## Experiments (owner's PC, Sarvam GLM-5.3, 19 Nov 2026)

**Hiring.** The request "hire someone for my inbox, call her Meera" was run headless as Smara, with every approval
declined so the real roster was unchanged.
- First manager prompt: one run went `team_list` then `team_hire`; one run stopped to ask four setup questions.
- Tuned prompt (hire with safe defaults, ask at most two questions only when the job is unclear): two of two runs went
  `team_list` then `team_hire` in about 20 s.
- The job texts were good: read mail every morning, triage, draft replies, never send, pay, post or delete without the
  owner's OK, never type passwords or OTPs.
- When declined, Smara reported that Meera was not hired.

**Speed.**
- A "hi" in a long chat sent about 65k uncached tokens and took 8–10 s.
- Sarvam's time barely depends on prompt size once the prompt has been seen: about 1.2–1.9 s at 2k, 16k and 45k tokens.
- GLM-5.3 at default reasoning: 1.3–1.8 s and about 130 reasoning tokens for a one-line reply. At low: 0.9–1.1 s and none.
- Changes made:
  - Chats run at low reasoning.
  - Long chats compact at 32k tokens.
  - Smara's own 2 KB base prompt replaces the 21 KB Codex CLI prompt.
  - No snapshot helper runs per message in projects without Git.
  - The memory lookup is skipped for greetings.

## Not done yet

- **Per-employee tool sets.** For example, no browser tools for the accountant. The runtime's tool list is fixed per
  launch, so this needs per-employee runtimes.
- **Employee schedules.** For example, "every morning at 9".
- **Channels** (Gmail, LinkedIn, Instagram, WhatsApp Business). They need connectors and the owner's sign-in. Until
  then employees work through the browser on the owner's PC, with approvals.
