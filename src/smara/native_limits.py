"""Inference admission limits for research, shared by a native worker tree.

No tools run here. Native execution/approvals remain authoritative. Once a
limit is reached one tool-free inference may synthesize existing evidence;
subsequent inference fails closed rather than buying an endless retry loop.
State stores hashes/call IDs, never prompts, URLs, credentials or tool results.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import json
import threading
import time


RESEARCH_TOOLS = frozenset({"web_search", "academic_search", "fetch_url", "source_page", "document_ocr", "image_analyze"})


def research_tool(name):
    return str(name).split("__")[-1] in RESEARCH_TOOLS


@dataclass
class Window:
    started: float
    calls: set = field(default_factory=set)
    repeats: dict = field(default_factory=dict)
    requests: int = 0
    research: bool = False
    synthesis: bool = False


class ResearchLimitError(RuntimeError):
    pass


class ResearchGuard:
    def __init__(self, *, worker_calls=64, shared_calls=240, worker_seconds=240,
                 shared_seconds=600, worker_requests=80, shared_requests=300,
                 repeat_limit=3, clock=time.monotonic):
        self.worker_calls, self.shared_calls = worker_calls, shared_calls
        self.worker_seconds, self.shared_seconds = worker_seconds, shared_seconds
        self.worker_requests, self.shared_requests = worker_requests, shared_requests
        self.repeat_limit, self.clock = repeat_limit, clock
        self.windows, self.groups = OrderedDict(), OrderedDict()
        self.lock = threading.Lock()

    def admit(self, request, payload, mapping, headers=None):
        metadata = request.get("client_metadata") or {}
        headers = headers or {}
        thread = metadata.get("thread_id") or headers.get("thread-id") or "anonymous"
        turn = metadata.get("turn_id")
        inputs = request.get("input", [])
        if not isinstance(inputs, list):
            inputs = [{"role": "user", "content": inputs}]
        # Fallback for older transports: isolate actual user turns, not child
        # status notifications. Native turn IDs survive context compaction.
        start = 0
        for index, item in enumerate(inputs):
            if item.get("role") == "user" and "<subagent_notification>" not in json.dumps(item.get("content", "")):
                start = index
        turn = turn or hashlib.sha256(json.dumps(inputs[start:start + 1], sort_keys=True).encode()).hexdigest()
        key = (thread, turn)
        group_key = metadata.get("root_turn_id") or metadata.get("parent_turn_id") or (turn if metadata.get("turn_id") else key)
        research_calls = [item for item in inputs[start:] if item.get("type") == "function_call" and research_tool(item.get("name"))]
        with self.lock:
            if not research_calls and group_key not in self.groups:
                return None
            now = self.clock()
            window = self._window(self.windows, key, now)
            group = self._window(self.groups, group_key, now)
            for item in research_calls:
                call_id = str(item.get("call_id"))
                if call_id in window.calls:
                    continue
                window.research = group.research = True
                window.calls.add(call_id)
                group.calls.add((thread, call_id))
                try:
                    arguments = json.loads(item.get("arguments", "{}"))
                except (ValueError, TypeError):
                    arguments = item.get("arguments")
                fingerprint = hashlib.sha256(json.dumps([item.get("name"), arguments], sort_keys=True).encode()).hexdigest()
                window.repeats[fingerprint] = window.repeats.get(fingerprint, 0) + 1
            # Coding-only turns and their legitimately repeated test runs are
            # untouched. Parents share research state once a child researches.
            if not window.research and not group.research:
                return None
            window.requests += 1
            group.requests += 1
            reason = None
            if max(window.repeats.values(), default=0) >= self.repeat_limit:
                reason = "Repeated identical research calls produced no new retrieval opportunity"
            elif len(window.calls) >= self.worker_calls or len(group.calls) >= self.shared_calls:
                reason = "Research tool-call budget reached"
            elif now - window.started >= self.worker_seconds or now - group.started >= self.shared_seconds:
                reason = "Research time budget reached"
            elif window.requests >= self.worker_requests or group.requests >= self.shared_requests:
                reason = "Research inference-request budget reached"
            if not reason and not window.synthesis:
                return None
            if window.synthesis:
                raise ResearchLimitError("Research limit reached; the bounded synthesis request was already used. No further provider request was sent.")
            window.synthesis = True
            # No emitted tool calls may reach the executor, even if a provider
            # ignores the tool-free request. Empty mapping fails closed there.
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
            mapping.clear()
            payload["messages"].append({"role": "system", "content":
                reason + ". Tools are now unavailable for this turn. Return your best partial answer or worker handoff from evidence already gathered. "
                "Explicitly state the research limit and unresolved gaps; distinguish fetched evidence from discovery snippets. "
                "Do not invent sources, imply the task fully succeeded, or ask to run another tool. If a worker report is missing, say it is missing."})
            return reason

    @staticmethod
    def _window(windows, key, now):
        if key not in windows:
            if len(windows) >= 256:
                # Do not evict active limits and inadvertently reset a budget.
                raise ResearchLimitError("Research admission state is full; reconnect before starting another turn.")
            windows[key] = Window(now)
        return windows[key]
