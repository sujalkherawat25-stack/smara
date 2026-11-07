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
import re
from dataclasses import dataclass, field

UNTRUSTED_ALLOWED = frozenset({"write_continuum_evidence", "propose_ledger_adjustment"})
BROWSER_READ = frozenset({"navigate", "read", "text", "scroll", "back", "wait", "hover"})
BROWSER_ACT = frozenset({"click", "fill", "press", "select"})


@dataclass(frozen=True)
class SessionCtx:
    id: str
    tenant_id: str
    trust: str                      # TRUSTED | UNTRUSTED | SYSTEM
    party_id: str | None = None     # UNTRUSTED: the only party this session may touch
    files: tuple[str, ...] = ()     # file refs attached to the triggering event
    call_id: str = ""
    grants: tuple[str, ...] = ()    # sites the owner let this session act on without a tap per click
    host: str = ""                 # the site the browser is on right now
    on_device: bool = False         # the browser is the owner's own PC, in front of them with a Stop button
    page: str = ""                 # lower-case url + title of the current page
    last_fill: str = ""            # ref of the last box the agent typed into in this conversation
    last_fill_value: str = ""      # and what it typed there
    controls: str = ""             # the numbered controls of the current page ('[12] button "Add to cart"' per line)


@dataclass(frozen=True)
class Decision:
    verdict: str                    # allow | deny | approve
    tier: str = ""
    reason: str = ""
    card: str = ""                  # what the owner sees on the approval card


# Steps that spend, send, delete, book or publish always need the owner's tap, wherever the browser is.
# (Opening a menu, adding to a cart, filtering, searching or logging-in pages are NOT in this list: the agent just does them.)
OUTWARD_STEP = re.compile(
    r"\b(pay|payment|pay now|buy|buy now|purchase|checkout|check out|place order|place my order|confirm order|order now|"
    r"submit|delete|remove account|transfer|send|send money|book now|confirm booking|subscribe|cancel order|withdraw|donate|"
    r"upi|net ?banking|publish|post)\b", re.I)
# Pages where any click or typing waits for the owner: signing in, paying, one-time codes.
SENSITIVE_PAGE = re.compile(r"login|log-in|signin|sign-in|sign in|checkout|payment|/pay|otp|password|captcha", re.I)


SEARCH_BOX = re.compile(r"search|query|\bq\b|find|filter|lookup|khoj", re.I)


def control(controls: str, ref) -> str:
    """'button "Add to cart"' for a ref, from the page listing the agent acted on ('' if it is not there)."""
    m = re.search(r"^\s*\[" + re.escape(str(ref).strip().strip("[]")) + r"\] (.+)$", controls or "", re.M)
    return re.sub(r' value=".*$', "", m.group(1)) if m else ""


def target_words(args: dict, ctx) -> str:
    """What a step acts on, in words, for the rules below: the control's role and name (or a name the agent wrote)."""
    named = control(ctx.controls, args["ref"]) if args.get("ref") not in (None, "") else ""
    if not named and args.get("text"):
        named = f'clickable "{args["text"]}"'
    return named or str(args.get("selector") or "")


def submits_typed_text(args: dict, ctx) -> bool:
    """Pressing Enter right after typing into anything that is not a search box sends what was typed (a chat message,
    a comment, a form). That is outward, exactly like clicking Send."""
    if args.get("action") != "press" or str(args.get("value", "")).lower() not in ("enter", "return", "numpadenter"):
        return False
    box = control(ctx.controls, ctx.last_fill) or ctx.last_fill
    return bool(ctx.last_fill) and not SEARCH_BOX.search(box)


def sensitive_step(args: dict, ctx=None) -> bool:
    """True when this step must not run on its own even where the agent is otherwise free to act."""
    words = target_words(args, ctx) if ctx is not None else str(args.get("selector") or "")
    return bool(OUTWARD_STEP.search(f"{words} {args.get('value', '')}"))


def tier_of(name: str, args: dict) -> str:
    if name in ("query_ledger_sql", "resolve_continuum_memory", "run_python", "get_current_time", "check_gstin",
                "build_payment_request", "list_followups", "web_search", "read_attachment", "make_document", "load_skill"):
        return "T0"
    if name in ("schedule_followup", "cancel_followup", "send_file_to_owner"):
        return "T1"
    if name == "inspect_and_import_file":
        return "T1" if args.get("mode") == "import" else "T0"
    if name == "write_continuum_evidence":
        return "T1"
    if name == "hand_off_to_owner":
        return "T2"
    if name == "browser_step":
        return "T0" if args.get("action") in BROWSER_READ else "T2"
    if name == "send_whatsapp_message":
        return "T1" if args.get("to") == "owner" else "T2"
    if name == "pc_files":
        return "T2" if args.get("action") == "write" else "T0"
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
    if name == "pc_files":
        if ctx.trust != "TRUSTED":
            return Decision("deny", tier, "only a conversation with the owner can reach the files on their PC")
        if args.get("action") not in ("list", "read", "write"):
            return Decision("deny", tier, "action must be list, read or write")
    if name == "read_attachment" and args.get("file_ref") not in ctx.files:
        return Decision("deny", tier, "only files attached to this conversation can be opened")
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
    if name == "hand_off_to_owner" and ctx.trust != "TRUSTED":
        return Decision("deny", tier, "only a conversation with the owner can hand a step to them")
    if tier in ("T0", "T1"):
        return Decision("allow", tier)
    if name == "browser_step" and ctx.trust == "TRUSTED" and not sensitive_step(args, ctx) and not submits_typed_text(args, ctx):
        # On the owner's own PC (visible, with a Stop button) the agent acts freely, except on pages where the owner must
        # act (sign-in, payment, one-time codes). On the server browser the owner approves, or allows a site for a while.
        on_page = bool(SENSITIVE_PAGE.search(ctx.page))
        if ctx.on_device and not on_page:
            return Decision("allow", "T2a", "autonomous on the owner's own browser")
        if ctx.host and ctx.host in ctx.grants and not on_page:
            return Decision("allow", "T2g", "the owner allowed this site for this task")
    return Decision("approve", tier, card=approval_text(name, args, {"page": ctx.controls}))


def _site(page: dict | None) -> str:
    """'linkedin.com/messaging (Messaging)': the REAL address (never a name guessed from it, which a look-alike
    domain could imitate), shortened to the first two path segments, plus the page title when it adds something."""
    from urllib.parse import urlparse
    page = page or {}
    u = urlparse(page.get("url", ""))
    host = (u.hostname or "").removeprefix("www.")
    title = re.sub(r"^\(\d+\)\s*", "", (page.get("title") or "").strip())       # "(1) Messaging | LinkedIn" -> "Messaging | LinkedIn"
    if not host:
        return title or "this page"
    where = host + "".join("/" + seg for seg in [x for x in u.path.split("/") if x][:2])
    part = title.split("|")[0].strip()
    return f"{where} ({part})" if part and part.lower() != host.split(".")[0] else where


def _described(line: str) -> str:
    """'button "Place order"' -> 'the “Place order” button'."""
    m = re.match(r'(\S+) "(.*?)"', line)
    if not m:
        return "a control"
    role, label = m.group(1), m.group(2)[:60]
    kind = {"button": "button", "link": "link", "textbox": "box", "searchbox": "search box", "combobox": "list",
            "checkbox": "tick box", "radio": "option", "tab": "tab", "menuitem": "menu item", "option": "option"}.get(role, "")
    if not label:
        return f"a {kind or 'control'}"
    return f"the “{label}” {kind}".rstrip() if kind else f"“{label}”"


def _target(selector: str) -> str:
    """A selector in words an owner understands: 'the "Send" button', 'the message box'."""
    sel = selector.strip()
    sel = sel.replace("\\", "")                                # models sometimes send JSON-escaped quotes
    found = (re.search(r"""\[(?:data-smara-name|aria-label|title|name|placeholder|value)\*?=["']?([^"'\]]+)""", sel)
             or re.search(r"^text=(.+)$", sel) or re.search(r""":has-text\(["']([^"']+)""", sel))
    label = found.group(1).strip().strip('"') if found else ""
    if not label and (ident := re.match(r"^#([\w-]+)$", sel)):
        label = ident.group(1).replace("-", " ").replace("_", " ")
    tag = re.match(r"[a-z]+", sel.lower())
    tag = tag.group(0) if tag else ""
    if 'role="textbox"' in sel or "contenteditable" in sel or "textbox" in sel:
        kind = "the message box"
    elif tag == "button" or "role=button" in sel.replace('"', ""):
        kind = f"the “{label}” button" if label else "a button"
    elif tag == "a":
        kind = f"the “{label}” link" if label else "a link"
    elif tag in ("input", "textarea"):
        kind = f"the “{label}” field" if label else "a text field"
    elif sel.startswith("text="):
        kind = f"“{label}”"
    else:
        kind = f"“{label}”" if label else "a control"
    return kind


def approval_text(name: str, args: dict, page: dict | None = None, typed: str = "") -> str:
    if name == "send_whatsapp_message":
        return f"Send this WhatsApp message?\n\n\"{str(args.get('text', ''))[:700]}\""
    if name == "propose_ledger_adjustment":
        parts = [str(args.get("kind", "")).replace("_", " ").lower()]
        if args.get("new_amount_rupees") is not None:
            parts.append(f"new amount Rs {args['new_amount_rupees']}")
        if args.get("new_due_date"):
            parts.append(f"pay by {args['new_due_date']}")
        return "Change this invoice? " + ", ".join(parts)
    if name == "pc_files":
        return (f"Save this file on your PC (Smara Shared folder)?\n\n{str(args.get('path') or '')[:120]}"
                f"{' (replaces the existing file)' if args.get('overwrite') else ''}\n\n\"{str(args.get('text') or '')[:400]}\"")
    if name == "hand_off_to_owner":
        return f"Your turn in the browser: {str(args.get('reason', ''))[:600]}\n\nDo it yourself, then tap Done and I will carry on."
    if name == "browser_step" and typed and args.get("action") == "press":
        return f"Smara is about to SEND this message on {_site(page)}:\n\n\"{typed[:600]}\"\n\nApprove to send it."
    if name == "browser_step":
        act, sel, val = args.get("action"), str(args.get("selector") or ""), str(args.get("value") or "")
        site = _site(page)
        line = control((page or {}).get("page", ""), args["ref"]) if args.get("ref") not in (None, "") else ""
        if not line and args.get("text"):
            line = f'clickable "{args["text"]}"'
        what = _described(line) if line else (_target(sel) if sel else "a control")
        if act == "click":
            return f"Smara wants to click {what} on {site}.\n\nApprove?"
        if act == "fill":
            return f"Smara wants to type into {what} on {site}:\n\n\"{val[:300]}\"\n\nApprove?"
        if act == "press":
            return f"Smara wants to press {val or 'Enter'} on {site}.\n\nApprove?"
        return f"Smara wants to {act} {what} on {site}.\n\nApprove?"
    return f"Allow {name}?"
