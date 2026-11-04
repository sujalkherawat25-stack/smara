"""Keep an agent session inside a bounded context without breaking the
tool_call/tool pairing providers require.

Order of cuts (cheapest first):
  1. old tool results and old tool-call arguments (big code, SQL rows) are truncated;
  2. if still over budget, the oldest whole turns are replaced by one note.
The last `keep_recent` messages and the system prompt are never touched, and a
turn (a user message plus everything the agent did for it) is dropped as a unit,
so an assistant tool call is never separated from its tool results.
"""
import json

NOTE = "[Earlier conversation omitted to save space: {n} messages. Re-query tools if you need old details.]"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _shrink_args(args_json: str, limit: int) -> str:
    try:
        args = json.loads(args_json)
        if isinstance(args, dict):
            return json.dumps({k: (_clip(v, limit) if isinstance(v, str) else v) for k, v in args.items()},
                              ensure_ascii=False)
    except ValueError:
        pass
    return _clip(args_json, limit)


def size(messages: list[dict]) -> int:
    return sum(len(m.get("content") or "") + sum(len(c["function"]["arguments"]) for c in m.get("tool_calls") or [])
               for m in messages)


def is_note(m: dict) -> bool:
    return m["role"] == "user" and (m.get("content") or "").startswith("[Earlier conversation omitted")


def compact(messages: list[dict], *, keep_recent: int = 10, tool_chars: int = 600, arg_chars: int = 500,
            budget_chars: int = 60_000) -> list[dict]:
    cutoff = len(messages) - keep_recent
    out = []
    for i, m in enumerate(messages):
        if i < cutoff and m["role"] == "tool":
            m = {**m, "content": _clip(m.get("content") or "", tool_chars)}
        elif i < cutoff and m["role"] == "assistant" and m.get("tool_calls"):
            m = {**m, "tool_calls": [{**c, "function": {**c["function"],
                                                       "arguments": _shrink_args(c["function"]["arguments"], arg_chars)}}
                                     for c in m["tool_calls"]]}
        out.append(m)
    omitted = 0
    while size(out) > budget_chars:
        turns = [i for i, m in enumerate(out) if m["role"] == "user" and not is_note(m)]
        if len(turns) < 2:
            break                                           # a single turn: nothing safe to drop
        start, end = turns[0], turns[1]
        omitted += end - start
        del out[start:end]
        out = [m for m in out if not is_note(m)]
        out.insert(1 if out and out[0]["role"] == "system" else 0, {"role": "user", "content": NOTE.format(n=omitted)})
    return out
