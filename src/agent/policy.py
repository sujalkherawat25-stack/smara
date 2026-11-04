"""PreToolUse: the single security boundary between the agent and the world.

Every tool call the model makes passes through `pre_tool_use` before it runs.
The model cannot reach a side effect any other way.

  T0 read      query ledger (read-only role + RLS), resolve memory, inspect file, browser navigate/read,
               run_python (isolated sandbox: no network, no ledger access, own files only)
  T1 internal  write evidence, import the owner's own file, message the owner, schedule/cancel the
               agent's own follow-ups, send the owner a file the sandbox produced
  T2 outward   message a counterparty, browser click/fill/press        -> owner approval
  T3 money     propose a ledger adjustment                              -> owner approval

UNTRUSTED sessions (started by a counterparty's message) may only record
evidence and propose an adjustment for their own party; everything else is
blocked here, whatever the model was talked into.
"""
from dataclasses import dataclass, field

UNTRUSTED_ALLOWED = frozenset({"write_continuum_evidence", "propose_ledger_adjustment"})
BROWSER_READ = frozenset({"navigate", "read"})
BROWSER_ACT = frozenset({"click", "fill", "press"})


@dataclass(frozen=True)
class SessionCtx:
    id: str
    tenant_id: str
    trust: str                      # TRUSTED | UNTRUSTED | SYSTEM
    party_id: str | None = None     # UNTRUSTED: the only party this session may touch
    files: tuple[str, ...] = ()     # file refs attached to the triggering event
    call_id: str = ""


@dataclass(frozen=True)
class Decision:
    verdict: str                    # allow | deny | approve
    tier: str = ""
    reason: str = ""
    card: str = ""                  # what the owner sees on the approval card


def tier_of(name: str, args: dict) -> str:
    if name in ("query_ledger_sql", "resolve_continuum_memory", "run_python", "get_current_time", "check_gstin",
                "build_payment_request", "list_followups"):
        return "T0"
    if name in ("schedule_followup", "cancel_followup", "send_file_to_owner"):
        return "T1"
    if name == "inspect_and_import_file":
        return "T1" if args.get("mode") == "import" else "T0"
    if name == "write_continuum_evidence":
        return "T1"
    if name == "browser_step":
        return "T0" if args.get("action") in BROWSER_READ else "T2"
    if name == "send_whatsapp_message":
        return "T1" if args.get("to") == "owner" else "T2"
    if name == "propose_ledger_adjustment":
        return "T3"
    return "?"


def pre_tool_use(ctx: SessionCtx, name: str, args: dict, known_tools: frozenset[str]) -> Decision:
    if name not in known_tools:
        return Decision("deny", reason=f"unknown tool {name!r}")
    if not isinstance(args, dict):
        return Decision("deny", reason="arguments must be a JSON object")
    tier = tier_of(name, args)
    if ctx.trust == "UNTRUSTED":
        if name not in UNTRUSTED_ALLOWED:
            return Decision("deny", tier, "this session was started by a counterparty and may only record "
                                          "evidence or propose an adjustment for the owner to review")
        if args.get("party_id", ctx.party_id) != ctx.party_id:
            return Decision("deny", tier, "a counterparty session may only touch its own party")
    if name == "browser_step" and args.get("action") not in BROWSER_READ | BROWSER_ACT:
        return Decision("deny", tier, "unsupported browser action")
    if name == "inspect_and_import_file":
        if args.get("file_ref") not in ctx.files:
            return Decision("deny", tier, "only files attached to this conversation can be opened")
        if tier == "T1" and ctx.trust != "TRUSTED":
            return Decision("deny", tier, "only the owner's own uploads can be imported")
    if name == "send_file_to_owner":
        if args.get("file_ref") not in ctx.files or not str(args.get("file_ref", "")).startswith("sandbox:"):
            return Decision("deny", tier, "only files you created with run_python in this conversation can be sent")
    if name == "run_python":
        refs = args.get("file_refs") or []
        if not isinstance(refs, list) or any(r not in ctx.files for r in refs):
            return Decision("deny", tier, "only files attached to this conversation can be opened")
        if not isinstance(args.get("code"), str) or not args["code"].strip() or len(args["code"]) > 20_000:
            return Decision("deny", tier, "code must be a non-empty string under 20,000 characters")
    if tier in ("T0", "T1"):
        return Decision("allow", tier)
    return Decision("approve", tier, card=approval_text(name, args))


def approval_text(name: str, args: dict, page: dict | None = None) -> str:
    if name == "send_whatsapp_message":
        return f"Send this WhatsApp message?\n\n\"{str(args.get('text', ''))[:700]}\""
    if name == "propose_ledger_adjustment":
        parts = [str(args.get("kind", "")).replace("_", " ").lower()]
        if args.get("new_amount_rupees") is not None:
            parts.append(f"new amount Rs {args['new_amount_rupees']}")
        if args.get("new_due_date"):
            parts.append(f"pay by {args['new_due_date']}")
        return "Change this invoice? " + ", ".join(parts)
    if name == "browser_step":
        where = f" on {(page or {}).get('title') or (page or {}).get('url')} ({(page or {}).get('url', '')[:80]})" if page else ""
        what = args.get("value") if args.get("action") in ("fill", "press") else args.get("selector")
        target = f" in {str(args.get('selector'))[:60]!r}" if args.get("action") == "fill" else ""
        return f"Allow the browser to {args.get('action')} {str(what)[:120]!r}{target}{where}?"
    return f"Allow {name}?"
