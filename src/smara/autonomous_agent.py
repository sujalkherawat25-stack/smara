"""
Autonomous ReAct Agent for Smara
Architecture:
- Multi-turn ReAct loop (Thought -> Action -> Observation -> Final Answer)
- Native tool calling with Sarvam GLM-5.3-flash & Gemma 4 on /v2/chat/completions
- Built-in tools: web_search, web_extract, wayback_extract, python_execute,
  file_read, zip_extract_and_read, calculate, memory, skills_list, skill_view, delegate_task
- Local Task Memory: durable file-backed memory with frozen system prompt caching
- Progressive Skills: dynamic discovery and loading of markdown skills
- Subagent Delegation: context-isolated worker execution
- 100% Genuine: Zero hardcoded cheat tables or keyword overrides.
"""

from __future__ import annotations
import base64
import json
import logging
import os
import re
import collections
import contextlib
import hashlib
import sys
import uuid
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import win32crypt
except ImportError:
    win32crypt = None

from smara.agent_tools import (
    web_search,
    web_extract,
    web_reader_dynamic,
    wayback_extract,
    python_execute,
    file_read,
    pdf_search,
    zip_extract_and_read,
    calculate,
    audio_transcribe,
    video_inspect,
    image_inspect,
    wikipedia_page,
    memory_tool,
    skills_list_tool,
    skill_view_tool,
    delegate_task_tool,
    dag_flow_tool,
    todo_tool,
    patch_file_tool,
    terminal_execute,
    file_write,
    browser_action_tool,
    list_directory,
    search_files,
    code_graph_tool,
)
from smara.task_memory import get_default_memory_store
from smara.task_planner import SmaraTaskPlanner
from smara.ptc_kernel import PTC_SAFE_TOOLS, ProgrammaticToolKernel

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("smara.autonomous_agent")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


IDEMPOTENT_TOOLS = frozenset({
    "programmatic_tool_call",
    "web_search",
    "web_extract",
    "web_reader_dynamic",
    "wayback_extract",
    "wikipedia_page",
    "file_read",
    "pdf_search",
    "calculate",
    "skills_list",
    "skill_view",
})

# H0 is deliberately conservative: delegation stays disabled until child
# policy and process isolation are enforced by the broker work.
DISABLED_TOOLS = frozenset({"delegate_task"})
VALID_TOOLSETS = frozenset({"full", "coding", "swe", "worker", "worker_coding", "worker_verification", "research", "web", "multimodal", "vision", "audio"})


def _tool_result_succeeded(observation: str) -> bool:
    """Conservative legacy-result decoder; never infer success from prose."""
    text = str(observation or "")
    exit_match = re.search(r"\[Exit Code:\s*(-?\d+)\]", text)
    if exit_match:
        return int(exit_match.group(1)) == 0
    if re.search(r"\b(error|failed|traceback|exception|timed out)\b", text, re.IGNORECASE):
        return False
    # Existing non-process tools have no typed receipt yet.  Do not use these
    # observations as verification evidence; this only avoids mislabelling a
    # plainly failed mutation as successful.
    return True


def _is_repetition_dominated(text: str, min_len: int = 400, window: int = 60, min_repeats: int = 5) -> bool:
    """Detect if a text fragment is dominated by verbatim repeated sequences (loop breaker)."""
    if not isinstance(text, str) or len(text) < min_len:
        return False
    n = len(text)
    # Fast check: repeated normalized lines covering significant portion
    counts: Dict[str, int] = collections.defaultdict(int)
    for line in text.splitlines():
        s = line.strip()
        if s:
            counts[s] += 1
            if counts[s] >= min_repeats and counts[s] * len(s) >= n * 0.4:
                return True
    # Sliding window check
    wcounts: Dict[str, int] = collections.defaultdict(int)
    needed = max(min_repeats, int(n * 0.4 / window))
    for i in range(n - window + 1):
        frag = text[i : i + window]
        wcounts[frag] += 1
        if wcounts[frag] >= needed:
            return True
    return False


def _extract_text_tool_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Extract tool calls from text whether formatted as JSON blocks or XML tags."""
    calls: List[Tuple[str, Dict[str, Any]]] = []
    if not isinstance(text, str) or not text.strip():
        return calls

    # Pattern 1: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    for m in re.finditer(r"<tool_call>\s*({[\s\S]*?})\s*</tool_call>", text):
        try:
            obj = json.loads(m.group(1))
            name = obj.get("name") or obj.get("function") or obj.get("tool")
            args = obj.get("arguments") or obj.get("parameters") or {}
            if name:
                calls.append((name, args if isinstance(args, dict) else {"query": str(args)}))
        except Exception:
            pass

    if calls:
        return calls

    # Pattern 2: <tool_call>...<(function|name|tool_name)>...</>...<(arguments|parameters)>...</>...</tool_call>
    for m in re.finditer(r"<tool_call>[\s\S]*?<(?:function|name|tool_name)>(\w+)</(?:function|name|tool_name)>[\s\S]*?<(?:arguments|parameters)>([\s\S]*?)</(?:arguments|parameters)>[\s\S]*?</tool_call>", text):
        name = m.group(1).strip()
        raw_args = m.group(2).strip()
        try:
            args = json.loads(raw_args)
        except Exception:
            args = {"command": raw_args} if name in ("terminal", "bash") else {"query": raw_args}
        calls.append((name, args))

    if calls:
        return calls

    # Pattern 3: ```json {"name": "...", "arguments": {...}} ```
    for m in re.finditer(r"```(?:json)?\s*({[\s\S]*?})\s*```", text):
        try:
            obj = json.loads(m.group(1))
            name = obj.get("name") or obj.get("function") or obj.get("tool")
            args = obj.get("arguments") or obj.get("parameters") or {}
            if name and isinstance(name, str):
                calls.append((name, args if isinstance(args, dict) else {"query": str(args)}))
        except Exception:
            pass

    return calls


def _compact_conversation_history(
    messages: List[Dict[str, Any]],
    max_chars: int = 40000,
    planner: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Three-Zone Context Compactor:
    Zone 1: Pinned Head (system prompt and original user task)
    Zone 2: Pinned Tail (most recent 4 messages)
    Zone 3: Middle Turns (compact large observations to preserve attention and token budget)
    Also preserves active task checklist from SmaraTaskPlanner across compaction.
    """
    total_chars = sum(len(str(m.get("content") or "")) for m in messages)
    if total_chars <= max_chars and not any(len(str(m.get("content") or "")) > 6000 for m in messages):
        return messages

    head_count = min(2, len(messages))
    tail_count = min(4, len(messages) - head_count)
    if len(messages) - tail_count > head_count and messages[len(messages) - tail_count].get("role") == "tool":
        tail_count += 1
    middle_messages = messages[head_count : len(messages) - tail_count] if len(messages) > head_count + tail_count else []

    compacted_middle: List[Dict[str, Any]] = []
    for m in middle_messages:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "tool" and len(content) > 600:
            compacted_m = dict(m)
            compacted_m["content"] = content[:300] + f"\n... [Context Compaction: {len(content)-450} chars omitted to preserve attention budget] ...\n" + content[-150:]
            compacted_middle.append(compacted_m)
        elif role == "assistant" and len(content) > 800:
            compacted_m = dict(m)
            compacted_m["content"] = content[:400] + "\n... [Assistant thought condensed] ...\n" + content[-200:]
            compacted_middle.append(compacted_m)
        else:
            compacted_middle.append(m)

    # If active task checklist exists, preserve it at the boundary between middle and tail
    if planner is not None and getattr(planner, "has_items", lambda: False)():
        active_snapshot = planner.format_for_injection()
        if active_snapshot:
            compacted_middle.append({
                "role": "user",
                "content": active_snapshot,
            })

    tail_messages = messages[len(messages) - tail_count:] if tail_count > 0 else []
    compacted_tail: List[Dict[str, Any]] = []
    for m in tail_messages:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "tool" and len(content) > 4000:
            compacted_m = dict(m)
            compacted_m["content"] = content[:2000] + f"\n... [Tool Observation Excerpt: {len(content)-2800} chars indexed in EvidenceIndex] ...\n" + content[-800:]
            compacted_tail.append(compacted_m)
        else:
            compacted_tail.append(m)

    packed = messages[:head_count] + compacted_middle + compacted_tail
    while sum(len(str(m.get("content") or "")) for m in packed) > max_chars and len(packed) > head_count + tail_count + 1:
        index = head_count
        if packed[index].get("role") == "tool" and index > head_count:
            packed.pop(index - 1)
            index -= 1
        packed.pop(index)

    overflow = sum(len(str(m.get("content") or "")) for m in packed) - max_chars
    if overflow > 0:
        for message in packed:
            content = str(message.get("content") or "")
            if overflow <= 0:
                break
            if len(content) > 256:
                remove = min(overflow, len(content) - 256)
                message["content"] = content[:len(content) - remove]
                overflow -= remove
    return packed


def _offload_massive_result(content: str, call_id: str, max_chars: int = 4000) -> str:
    """If tool output is massive, persist full output to cache directory and return a clean excerpt."""
    if len(content) <= max_chars:
        return content
    try:
        cache_dir = Path("data/cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{call_id}.txt"
        cache_file.write_text(content, encoding="utf-8", errors="replace")
        half = max_chars // 2 - 200
        return content[:half] + f"\n\n... [Output exceeds inline limit ({len(content)} characters). Full result cached to {cache_file.as_posix()}. Use python_execute or file_read to inspect/slice] ...\n\n" + content[-half:]
    except Exception:
        return content[:max_chars] + f"\n... [Truncated {len(content) - max_chars} characters]"


def _is_instruction_placeholder(text: str) -> bool:
    """Detect if string is a prompt instruction placeholder or internal tag rather than a genuine answer."""
    if not text:
        return True
    t = text.strip().lower()
    t_clean = re.sub(r"^[\<\\[\(\"']+|[\>\\]\)\"']+$", "", t).strip()
    placeholders = {
        "exact answer", "answer", "final answer", "your answer",
        "insert answer here", "insert answer", "value", "exact answer here",
        "result", "exact result", "undefined", "n/a", "none",
        "reached", "final answer reached", "step", "completed", "pending", "in_progress", "thought"
    }
    if t_clean in placeholders or t in placeholders:
        return True
    if re.match(r"^<[a-z0-9_\s\-]+>$", t) or re.match(r"^\[[a-z0-9_\s\-]+\]$", t):
        return True
    return False


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "programmatic_tool_call",
            "description": "Batch up to 8 independent read-only or calculation tools in one turn. This never runs shell commands, writes files, changes memory, uses credentials, delegates work, or controls a browser. Use it to gather several facts efficiently, then reason over the returned observations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "calls": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "description": "Safe tool calls to execute sequentially.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "enum": sorted(PTC_SAFE_TOOLS),
                                },
                                "args": {
                                    "type": "object",
                                    "description": "Arguments for the selected tool.",
                                    "additionalProperties": True,
                                },
                            },
                            "required": ["name", "args"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["calls"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live web using Tavily / Google / DuckDuckGo for factual, up-to-date, or historical information. Supports single query or concurrent batch queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query keywords."
                    },
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of search queries to execute concurrently in parallel."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of search results to return per query (default 5).",
                        "default": 5
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_extract",
            "description": "Fetch and parse readable text content from web URLs or Wikipedia articles. Supports single URL or concurrent batch URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full HTTP or HTTPS URL to fetch."
                    },
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of URLs to fetch and extract concurrently in parallel."
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters per page (default 5000).",
                        "default": 5000
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_reader_dynamic",
            "description": "Fetch and render dynamic JavaScript-heavy web pages, SPAs, modern documentation, or complex sites (using headless browser rendering) into clean Markdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full HTTP or HTTPS URL to fetch dynamically."
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (default 16000).",
                        "default": 16000
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wayback_extract",
            "description": "Retrieve historical snapshots of a webpage from the Internet Archive Wayback Machine around a specific date (YYYYMMDD). Essential for questions asking about past versions of web pages, former titles, or old Wikipedia revisions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the webpage."
                    },
                    "timestamp": {
                        "type": "string",
                        "description": "Target date string in YYYYMMDD or YYYY format (e.g. '20230501')."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia_page",
            "description": "Fetch current or historical Wikipedia articles, count revisions, or list images using the official Wikipedia MediaWiki API. Actions: 'text' (article text at date or current), 'revisions_count' (count revisions before date), 'images' (count/list content images at date).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_or_url": {
                        "type": "string",
                        "description": "Wikipedia article title or full URL."
                    },
                    "date_or_timestamp": {
                        "type": "string",
                        "description": "Optional cutoff date/timestamp (e.g., '2022', '2022-12-31', '2019-05-01')."
                    },
                    "action": {
                        "type": "string",
                        "enum": ["text", "revisions_count", "images"],
                        "description": "Action to perform (default: 'text').",
                        "default": "text"
                    }
                },
                "required": ["title_or_url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "python_execute",
            "description": "Execute arbitrary Python 3 code in an isolated subprocess to perform precise math, parsing, data transformation, counting, regex, or logic. Must use print() to output results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Valid Python code to execute."
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read contents of a file with line numbers and optional line-range windowing. Supports all code and text formats (.py, .rs, .ts, .txt, .json, .csv, .md, etc.) as well as PDF, DOCX, XLSX.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to inspect."
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Optional starting line number (1-indexed) for windowed reading.",
                        "default": 1
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional maximum number of lines to return from offset (default: 100).",
                        "default": 100
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (default 12000).",
                        "default": 12000
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pdf_search",
            "description": "Search across all pages of a PDF file for specific keywords, or extract full page text and diagram descriptions for specific pages (by setting 'page' or 'start_page'/'end_page' with empty query).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {
                        "type": "string",
                        "description": "Path to the .pdf file."
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional keyword or phrase to search for across the PDF. If empty, extracts page text directly.",
                        "default": ""
                    },
                    "page": {
                        "type": "integer",
                        "description": "Specific 1-indexed page number to extract text and diagram/figure descriptions from."
                    },
                    "start_page": {
                        "type": "integer",
                        "description": "Starting page number (1-indexed, default 1).",
                        "default": 1
                    },
                    "end_page": {
                        "type": "integer",
                        "description": "Ending page number (inclusive). If omitted, searches through the end of the document."
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "Maximum number of matches to return (default 10).",
                        "default": 10
                    }
                },
                "required": ["pdf_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "zip_extract_and_read",
            "description": "List or extract contents of a zip archive attached to a task. If target_file is omitted, lists all entries inside the archive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "zip_path": {
                        "type": "string",
                        "description": "Path to the .zip archive file."
                    },
                    "target_file": {
                        "type": "string",
                        "description": "Specific relative file inside the zip archive to read. If omitted, lists archive files."
                    }
                },
                "required": ["zip_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Safely evaluate a mathematical formula or expression (e.g., '((14.5 * 12) + 180) / 4').",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The math expression to evaluate."
                    }
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "audio_transcribe",
            "description": "Transcribe speech from an audio file (.mp3, .wav, .m4a) or online audio/video URL into timestamped text using Whisper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path_or_url": {
                        "type": "string",
                        "description": "Path to local audio file or online media URL."
                    }
                },
                "required": ["file_path_or_url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "video_inspect",
            "description": "Inspect a YouTube video or local video file. Actions: 'transcript' to get full speech transcript; 'info' for metadata/duration; 'frame' to extract and visually inspect a frame at timestamp_seconds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url_or_path": {
                        "type": "string",
                        "description": "YouTube video URL or local video path."
                    },
                    "action": {
                        "type": "string",
                        "enum": ["transcript", "info", "frame"],
                        "description": "Inspection action to perform.",
                        "default": "transcript"
                    },
                    "timestamp_seconds": {
                        "type": "number",
                        "description": "Timestamp in seconds for frame extraction (required if action='frame')."
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Specific visual question or text to look for in the frame."
                    }
                },
                "required": ["url_or_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "image_inspect",
            "description": "Visually inspect an image, photo, screenshot, or diagram using Gemma 4 multimodal vision. Returns transcription of text and detailed visual descriptions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Path to local image file (.png, .jpg, .jpeg, .webp)."
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Specific question or instructions on what to extract from the image."
                    }
                },
                "required": ["image_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory",
            "description": "Read, add, replace, or search durable curated memory (MEMORY.md for project notes, USER.md for user preferences).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "replace", "remove", "search", "list"],
                        "description": "Action to perform on memory store."
                    },
                    "target": {
                        "type": "string",
                        "enum": ["memory", "user"],
                        "default": "memory",
                        "description": "Target store: 'memory' for project notes, 'user' for user profile."
                    },
                    "content": {
                        "type": "string",
                        "description": "Memory text to add or replacement text."
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Unique substring matching the entry to replace or remove."
                    },
                    "query": {
                        "type": "string",
                        "description": "Query term when searching memory."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skills_list",
            "description": "List available specialized skills with compact metadata (name, description, tags). Progressive disclosure Tier 1.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag_filter": {
                        "type": "string",
                        "description": "Optional tag to filter skills (e.g., 'git', 'web')."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_view",
            "description": "View detailed instructions (SKILL.md) or referenced assets for a specific skill. Progressive disclosure Tier 2 & 3.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "The unique name of the skill to inspect."
                    },
                    "relative_path": {
                        "type": "string",
                        "description": "Optional relative path to a supporting document (e.g., 'references/api.md')."
                    }
                },
                "required": ["skill_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Spawn an isolated worker subagent to autonomously research, code, test, or execute a sub-task without cluttering the parent context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Specific, actionable objective for the worker subagent."
                    },
                    "context": {
                        "type": "string",
                        "description": "Relevant background context and constraints."
                    },
                    "role": {
                        "type": "string",
                        "enum": ["generalist", "researcher", "coder", "tester", "auditor"],
                        "default": "generalist",
                        "description": "Specialized role for the worker subagent."
                    }
                },
                "required": ["goal"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dag_flow",
            "description": "Construct and execute a Directed Acyclic Graph (DAG) workflow for complex multi-stage tasks requiring dependency resolution and node pipelines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create_and_run"],
                        "default": "create_and_run",
                        "description": "DAG workflow action."
                    },
                    "workflow_data": {
                        "type": "string",
                        "description": "JSON string containing nodes list (each with id, title, capability, payload, depends_on)."
                    }
                },
                "required": ["action", "workflow_data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Manage your task checklist for the current session. Use for complex tasks with 3+ steps or when executing multi-phase plans. Call with no parameters to read the current list. List order is priority. Only one item in_progress at a time. Active tasks are automatically preserved across context compression events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "Task items to write.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Unique task ID."},
                                "content": {"type": "string", "description": "Task description."},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed", "cancelled"]
                                },
                                "parent": {
                                    "type": "string",
                                    "description": "Optional parent item ID for hierarchical subtasks."
                                }
                            },
                            "required": ["id", "content", "status"]
                        }
                    },
                    "merge": {
                        "type": "boolean",
                        "description": "true: update existing items by id and append new ones. false (default): replace entire checklist.",
                        "default": False
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "patch",
            "description": "Targeted find-and-replace edit on a file. Uses multi-strategy fuzzy matching (handles minor whitespace and indentation variations) and automatically performs Python AST syntax checks. Returns a unified diff of applied changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to file to edit."
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to find and replace. Must be unique in the file unless replace_all=true. Include surrounding context lines to ensure uniqueness."
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement text. To delete matched text, pass empty string ''."
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences instead of requiring a unique match (default false).",
                        "default": False
                    }
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Execute a shell command (PowerShell on Windows, bash on Unix) with timeout and output capture. Use for running test suites (pytest), build systems (cargo, npm), git commands, or linters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command line string to execute."
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory in which to execute the command."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds (default 45).",
                        "default": 45
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Directly create or overwrite a file with given content, automatically creating parent directories. For editing existing files, prefer 'patch'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target file path to write."
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write to the file."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_action",
            "description": "Autonomous headless browser automation for web pages. Actions: 'scrape' to fetch title/headings/clean text; 'screenshot' to capture visual page snapshot to disk; 'dom_snapshot' to inspect DOM structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to interact with."
                    },
                    "action": {
                        "type": "string",
                        "enum": ["scrape", "screenshot", "dom_snapshot"],
                        "default": "scrape",
                        "description": "Browser action to execute."
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Optional file path to save screenshot when action='screenshot'."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List directory contents in a compact, structured tree format. Use to discover project layouts, locate source files, and explore directory structures.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (default: current directory '.').",
                        "default": "."
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum directory traversal depth (default: 2).",
                        "default": 2
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search file contents across a codebase or directory using ripgrep or regex. Returns filename:line_number: match snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search string or regex pattern."
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search in (default: '.').",
                        "default": "."
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "Whether query should be treated as regex (default: false).",
                        "default": False
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "code_graph",
            "description": "Query the AST Code Property Graph for instant symbol definition, callers, references, or blast radius across the codebase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["inspect_symbol", "blast_radius", "find_references"],
                        "description": "Operation to perform.",
                        "default": "inspect_symbol"
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Function, class, method, or symbol name to inspect."
                    }
                },
                "required": ["symbol"]
            }
        }
    }
]

# Canonical research actions keep planning, retrieval, provenance and claim
# acceptance inside the same durable session as every other model/tool step.
TOOL_SCHEMAS.extend([
    {"type":"function","function":{"name":"research_plan","description":"Create a dependency-aware research question graph before retrieval.","parameters":{"type":"object","additionalProperties":False,"required":["question","nodes"],"properties":{"question":{"type":"string"},"nodes":{"type":"array","maxItems":24,"items":{"type":"object","additionalProperties":False,"required":["id","question"],"properties":{"id":{"type":"string"},"question":{"type":"string"},"dependencies":{"type":"array","items":{"type":"string"}},"stopping_criterion":{"type":"string"}}}}}}}},
    {"type":"function","function":{"name":"research_search","description":"Search leads for one ready research node. Snippets are discovery-only.","parameters":{"type":"object","additionalProperties":False,"required":["node_id","query"],"properties":{"node_id":{"type":"string"},"query":{"type":"string"},"max_results":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"research_fetch","description":"Fetch a lead and preserve original response bytes plus extracted passage provenance.","parameters":{"type":"object","additionalProperties":False,"required":["node_id","url"],"properties":{"node_id":{"type":"string"},"url":{"type":"string"}}}}},
    {"type":"function","function":{"name":"research_ingest_file","description":"Ingest UTF-8 text/Markdown/CSV/JSON, extract a PDF table cell, or extract image OCR evidence from a workspace file while preserving the original artifact.","parameters":{"type":"object","additionalProperties":False,"required":["node_id","path"],"properties":{"node_id":{"type":"string"},"path":{"type":"string"},"page":{"type":"integer"},"row":{"type":"integer"},"column":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"research_inspect","description":"Inspect an evidence passage and verify its recoverable source artifact.","parameters":{"type":"object","additionalProperties":False,"required":["evidence_id"],"properties":{"evidence_id":{"type":"string"},"max_chars":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"research_analyze","description":"Compute provenance-bound descriptive statistics, grouped metrics, correlations, time changes, and IQR outliers from structured rows. Results are deterministic and stored as an immutable artifact; missing values are never imputed.","parameters":{"type":"object","additionalProperties":False,"required":["rows","numeric_columns","evidence_ids"],"properties":{"rows":{"type":"array","maxItems":10000,"items":{"type":"object"}},"numeric_columns":{"type":"array","maxItems":20,"items":{"type":"string"}},"evidence_ids":{"type":"array","minItems":1,"maxItems":20,"items":{"type":"string"}},"group_by":{"type":"string"},"time_column":{"type":"string"}}}}},
    {"type":"function","function":{"name":"research_resolve","description":"Resolve a question only through conservative claim/evidence judgments.","parameters":{"type":"object","additionalProperties":False,"required":["node_id","claim","evidence_ids"],"properties":{"node_id":{"type":"string"},"claim":{"type":"string"},"evidence_ids":{"type":"array","maxItems":20,"items":{"type":"string"}}}}}},
    {"type":"function","function":{"name":"research_validate","description":"Validate the final required claim/evidence map; unsupported claims prevent completion.","parameters":{"type":"object","additionalProperties":False,"required":["claims"],"properties":{"claims":{"type":"array","maxItems":40,"items":{"type":"object","additionalProperties":False,"required":["claim","evidence_ids"],"properties":{"claim":{"type":"string"},"evidence_ids":{"type":"array","maxItems":20,"items":{"type":"string"}}}}},"require_complete":{"type":"boolean"}}}}},
])
TOOL_SCHEMAS.extend([
    {"type":"function","function":{"name":"process_start","description":"Start a durable session-owned process. Starting is not task completion; poll and independently validate its effects.","parameters":{"type":"object","additionalProperties":False,"required":["argv","cwd"],"properties":{"argv":{"type":"array","maxItems":64,"items":{"type":"string"}},"cwd":{"type":"string"},"timeout_seconds":{"type":"number"},"env":{"type":"object"}}}}},
    {"type":"function","function":{"name":"process_poll","description":"Read one bounded process-log chunk from a cursor and inspect terminal state.","parameters":{"type":"object","additionalProperties":False,"required":["process_id"],"properties":{"process_id":{"type":"string"},"cursor":{"type":"integer"},"max_chars":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"process_stdin","description":"Write text to a running session-owned interactive process.","parameters":{"type":"object","additionalProperties":False,"required":["process_id","text"],"properties":{"process_id":{"type":"string"},"text":{"type":"string"}}}}},
    {"type":"function","function":{"name":"process_cancel","description":"Cancel a session-owned process tree and return its terminal receipt.","parameters":{"type":"object","additionalProperties":False,"required":["process_id"],"properties":{"process_id":{"type":"string"}}}}},
])
TOOL_SCHEMAS.extend([
    {"type":"function","function":{"name":"browser_open","description":"Open a fresh isolated managed-browser context and navigate to a scoped URL.","parameters":{"type":"object","additionalProperties":False,"properties":{"url":{"type":"string"}}}}},
    {"type":"function","function":{"name":"browser_observe","description":"Capture current DOM-grounded element references, text and screenshot artifact.","parameters":{"type":"object","additionalProperties":False,"properties":{}}}},
    {"type":"function","function":{"name":"browser_navigate","description":"Navigate the owned browser to an HTTP(S), about, data, or workspace file URL.","parameters":{"type":"object","additionalProperties":False,"required":["url"],"properties":{"url":{"type":"string"}}}}},
    {"type":"function","function":{"name":"browser_act","description":"Act on a current observed element reference.","parameters":{"type":"object","additionalProperties":False,"required":["observation_id","ref","action"],"properties":{"observation_id":{"type":"string"},"ref":{"type":"string"},"action":{"type":"string","enum":["click","fill","select","check","upload"]},"value":{}}}}},
    {"type":"function","function":{"name":"browser_tabs","description":"List stable tabs in the owned browser session.","parameters":{"type":"object","additionalProperties":False,"properties":{}}}},
    {"type":"function","function":{"name":"browser_switch","description":"Switch to an owned stable tab ID and observe it.","parameters":{"type":"object","additionalProperties":False,"required":["tab_id"],"properties":{"tab_id":{"type":"string"}}}}},
    {"type":"function","function":{"name":"browser_scroll","description":"Scroll the current page and return a fresh observation.","parameters":{"type":"object","additionalProperties":False,"properties":{"dy":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"browser_download","description":"Download from an observed link to a validated workspace-relative destination.","parameters":{"type":"object","additionalProperties":False,"required":["observation_id","ref","destination"],"properties":{"observation_id":{"type":"string"},"ref":{"type":"string"},"destination":{"type":"string"}}}}},
    {"type":"function","function":{"name":"browser_close","description":"Close the owned managed-browser context.","parameters":{"type":"object","additionalProperties":False,"properties":{}}}},
])


def get_tool_schemas(profile: str = "full") -> List[Dict[str, Any]]:
    """Return tool schemas filtered by profile to optimize token budget."""
    prof = (profile or "full").lower().strip()
    if prof not in VALID_TOOLSETS:
        # Schema callers can inspect an unsupported profile without falling
        # back to full authority. Agent construction rejects it below.
        return []
    if prof == "full":
        return [s for s in TOOL_SCHEMAS if s.get("function", {}).get("name") not in DISABLED_TOOLS]
    elif prof in ["coding", "swe"]:
        allowed = {
            "terminal", "file_write", "patch", "python_execute", "file_read",
            "list_directory", "search_files", "code_graph",
            "todo", "delegate_task", "dag_flow", "programmatic_tool_call", "process_start", "process_poll", "process_stdin", "process_cancel"
        }
    elif prof == "worker_coding":
        allowed = {
            "terminal", "file_write", "patch", "python_execute", "file_read",
            "list_directory", "search_files", "code_graph",
            "todo", "programmatic_tool_call", "process_start", "process_poll", "process_stdin", "process_cancel"
        }
    elif prof in {"worker", "worker_verification"}:
        allowed = {
            "terminal", "file_read", "list_directory", "search_files", "code_graph",
            "python_execute", "calculate", "browser_action", "web_search",
            "programmatic_tool_call", "todo"
        }
    elif prof == "research":
        allowed = {
            "browser_action", "pdf_search", "calculate",
            "file_read", "list_directory", "programmatic_tool_call", "todo",
            "research_plan", "research_search", "research_fetch", "research_inspect",
            "research_ingest_file", "research_analyze", "research_resolve", "research_validate"
            ,"browser_open","browser_observe","browser_navigate","browser_act","browser_tabs","browser_switch","browser_scroll","browser_download","browser_close"
            ,"process_start","process_poll","process_stdin","process_cancel"
        }
    elif prof == "web":
        allowed = {"browser_action","web_search","web_extract","web_reader_dynamic","wayback_extract","wikipedia_page","pdf_search","calculate","file_read","list_directory","programmatic_tool_call","todo"}
    elif prof in ["multimodal", "vision", "audio"]:
        allowed = {"browser_action", "image_inspect", "audio_transcribe", "video_inspect", "file_read", "todo"}
    return [s for s in TOOL_SCHEMAS if s.get("function", {}).get("name") in allowed - DISABLED_TOOLS]


BASE_SYSTEM_PROMPT = """You are Smara Autonomous Agent, an elite autonomous AI system.
You solve complex multi-step reasoning, research, multimodal, coding, and mathematical tasks autonomously using tool execution.

### Operational Guidelines:
1. **ReAct Problem Solving**:
   - Break down problems methodically: Thought -> Action -> Observation -> Final Answer.
   - For multi-step tasks (3+ steps) or complex coding/research trajectories, maintain a task checklist via `todo`. Active tasks survive context compaction.
   - For creating files, use `file_write`. For surgical edits on existing files, always use `patch`.
   - For running terminal commands, test suites, builds, or git, use `terminal`.
   - For headless browser actions, screenshots, or scraping, use `browser_action`.
   - Keep internal reasoning concise and focused (under 150 words) before executing tools or stating answers.
   - For quick factual web lookup, use `web_search` and `web_extract`. For evidence-backed research, use the canonical `research_plan` -> `research_search` -> `research_fetch` -> `research_resolve` -> `research_validate` path so snippets cannot become proof. Keep plan nodes simple and independent (e.g. 1-2 root nodes without dependencies). In research tasks, always include the public source URL(s) and end with FINAL LABEL: supported, refuted, or insufficient.
   - For quantitative claims from CSV/JSON/table data: first call `research_plan` with a node, then `research_fetch` the dataset URL, then `research_analyze` on the fetched rows, then `research_resolve` that node with the computed claim and evidence ID, then `research_validate`, and finally state your result, the dataset URL, and FINAL LABEL: supported.
   - When two or more independent read-only facts are needed, use `programmatic_tool_call` to batch them in one turn. Its allowlist is strict: never use it for shell commands, writes, memory changes, credentials, delegation, or browser control.
   - For historical snapshots of web pages, use `wayback_extract`.
   - For current or historical Wikipedia articles, revision histories, or image counts, use `wikipedia_page`.
   - For arithmetic, statistical calculations, data processing, regex, geometry, or counting, ALWAYS execute Python code via `python_execute` or `calculate` instead of estimating.
   - For local attached files, use `file_read` or `zip_extract_and_read`.
   - For audio recordings (.mp3, .wav), use `audio_transcribe`.
   - For YouTube videos or video files, use `video_inspect` (actions: 'transcript', 'info', 'frame'). When asked what appears at a specific timestamp, use action='frame' with `timestamp_seconds=N`.
   - For images, charts, diagrams, and photos, use `image_inspect` or `file_read`.
   - For complex modular tasks, delegate sub-goals using `delegate_task`.
   - For multi-stage dependency workflows or parallel task graphs, construct and execute DAGs using `dag_flow`.
   - To consult domain-specific guidelines, check `skills_list` and load instructions via `skill_view`.
   - When learning important project facts or user preferences, save them via `memory`.

2. **Procedural Problem-Solving Methodologies**:
   - **Ciphers & Decryption**: When decrypting Caesar ciphers or substitution ciphers, decrypt the exact characters strictly by shift offset using `python_execute`. You MUST output the exact decrypted characters produced by code VERBATIM. Do NOT alter, autocorrect, or "fix" any unusual spellings or names. Retain trailing punctuation (like periods '.') exactly as decrypted.
   - **Cross-Platform Scripting**: When writing Python scripts to download files or PDFs, use `tempfile.gettempdir()`, `io.BytesIO()`, or the current directory. NEVER use hardcoded Unix paths like `/tmp/` because they fail on Windows.
   - **String & Character Counting**: Always use Python code (`.count()`, `len()`) via `python_execute` to count letters, words, lines, or characters from text or image transcriptions to avoid manual counting mistakes.
   - **Ancient & Positional Numeral Systems**: For ancient numerals, non-standard glyphs, or positional notations (such as sexagesimal or Roman numerals), write a Python script with `unicodedata.name()` to inspect the exact characters and calculate values mathematically using base expansion: value = sum(d_i * B**i).
   - **Table Ranking & Extraction**: When comparing or ranking tabular data from websites (like charts, rankings, population lists, or statistics), fetch the table with `web_reader_dynamic` or `web_extract` and load it into a `pandas.DataFrame` or `BeautifulSoup` in `python_execute` to programmatically filter, sort, and slice rows rather than reading visual rankings manually.
   - **Large Document & PDF Deep Search**: When inspecting or searching across PDFs, use `pdf_search` with targeted keywords (e.g. author name, citation number, title phrase) or inspect specific pages directly with `pdf_search(pdf_path, page=N)`. It automatically reconciles physical PDF pages with printed book pages and reports embedded diagrams. Alternatively, write a Python script with `pypdf` to process pages systematically.
   - **Algebraic Word Problems & Multi-Variable Systems**: Decompose complex multi-variable word problems into individual facts. Use web search or tools to independently verify each constant/variable, then invoke `sympy` or `scipy` in `python_execute` to solve the system of equations.
   - **Dynamic SPAs & JavaScript Web Pages**: For websites that use client-side rendering (SPA frameworks, interactive listings, dynamically loaded tables), use `web_reader_dynamic` which renders JavaScript via markdown reader endpoints.
   - **Video Inspection & Timestamps**: When asked about a specific visual detail at timestamp T, inspect frames across a short temporal window (T-1, T, T+1, T+2) using `video_inspect(action='frame')` to account for video keyframe cuts and transitions.
   - **2D Geometry & Visual Dimension Decomposition**: For complex multi-segment 2D polygons or architectural layouts, partition the shape into disjoint bounding rectangles or triangles, determine the missing edge lengths using parallel edge arithmetic, and compute total area by summing sub-regions in `python_execute`.
   - **Dense Tabular PDF Extraction**: When analyzing dense multi-column tables, standards documents, or statistical tables in PDFs (>10 rows or multiple columns), DO NOT attempt to visually align columns in conversational reasoning. Instead, write a Python script via `python_execute` (using `pypdf`, `re`, or `pandas`) to parse rows, match column delimiters with regular expressions, and aggregate counts, averages, or conditions programmatically.
   - **Temporal Historical Profiles & Live APIs**: When querying author publication records, repository statistics, or profile histories for questions set in a specific historical context or past benchmark year, remember that live REST APIs (e.g. ORCID, GitHub, Wikipedia) return current data which may have grown over time. Always inspect entry dates (`publication_date <= YYYY`) programmatically in `python_execute` or check historical snapshots via `wayback_extract` when historical consistency is required.
   - **Parallel Multi-Query Batching**: When investigating multiple entities, standards, citations, or records (e.g. 5+ items), pass a list to `queries` in `web_search` or `urls` in `web_extract`, or write a concurrent Python script via `python_execute` (using `urllib` and `concurrent.futures.ThreadPoolExecutor`) to inspect all items concurrently in a single turn instead of querying one by one.
   - **Progressive Scripted Accumulator**: For questions requiring checking status across multiple items (e.g. whether N standards are superseded, or aggregating publication totals), store the findings in a Python data structure via `python_execute` and calculate the final percentage, sum, or count deterministically in code (e.g. `print(round(len(superseded) / total * 100))`).


3. **Honesty and Verification**:
   - Never fabricate or guess facts, URLs, dates, or calculations.
   - Verify every intermediate step with real tool outputs.

4. **Strict Final Answer Delivery Format**:
   - For standard coding/benchmark tasks, provide your definitive answer on the final line strictly as:
     FINAL ANSWER: <exact answer>
   - For research tasks, provide your concise synthesis, explicitly cite the public source URL(s) or live dataset URL, and end on the final line with:
     FINAL LABEL: <supported|refuted|insufficient>
   - Provide ONLY the direct, concise answer value required by the question.
   - Do NOT include conversational filler, explanations, justifications, or prefixes (such as 'the answer is', 'the result is').
   - For numerical questions with units (e.g. 'Report the answer in kilograms...'), report ONLY the bare number in that requested unit without adding unit symbols or text (e.g. 42.5, NOT 42.5 kg).
   - If asked for a specific character, word, or name, output ONLY that exact element without decoration.
   - If asked for comma-separated or semicolon-separated items, list ONLY the items cleanly in the requested order.
   - When asked "how many percent above or below [standard]% is [actual]%", report the direct difference in percentage points (i.e. actual% - standard%, such as +4.6 or -2.1), not relative growth ((actual-standard)/standard*100).
   - When asked "how many thousand X" or "how many million X", report the numerical quantity directly in that scaled unit (e.g. for 25,000 when asked "how many thousand", report 25, NOT 25000; for 5,000,000 when asked "how many million", report 5, NOT 5000000).
"""


def _get_api_key_from_vault_or_env() -> str:
    key = os.getenv("SMARA_MODEL_SARVAM_API_KEY") or os.getenv("SARVAM_API_KEY") or ""
    if key:
        return key
    try:
        cred_path = Path(r"C:\Users\sujal\AppData\Roaming\Smara\credentials.json")
        if cred_path.exists() and win32crypt:
            with open(cred_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entry = data.get("SMARA_MODEL_SARVAM_API_KEY", {})
            protected = entry.get("protected")
            if protected:
                blob = base64.b64decode(protected)
                _, decrypted = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
                return decrypted.decode("utf-8")
    except Exception:
        pass
    return ""


class SmaraAutonomousAgent:
    """Autonomous ReAct agent interacting with LLM models via multi-turn tool-calling loops."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.sarvam.ai/v2/chat/completions",
        model: str = "glm5.2",
        auth_header: str = "authorization",
        max_iterations: int = 25,
        toolset: str = "full",
        profile: Optional[str] = None,
        workspace_root: Optional[Path | str] = None,
        on_progress: Optional[Any] = None,
        session_engine: Optional[Any] = None,
    ):
        self.api_key = api_key or _get_api_key_from_vault_or_env()
        self.base_url = base_url
        self.model = model
        self.auth_header = auth_header.lower().strip()
        self.max_iterations = max_iterations
        self.toolset = profile or toolset
        if self.toolset not in VALID_TOOLSETS:
            raise ValueError(f"Unknown tool profile '{self.toolset}'.")
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd()
        self.on_progress = on_progress
        self.session_engine = session_engine
        self.task_planner = SmaraTaskPlanner()
        self.memory_store = get_default_memory_store()
        self._seen_tool_signatures: Dict[str, int] = collections.defaultdict(int)
        from smara.harness import ToolBroker
        process_root=(session_engine.root/session_engine.session_id/"processes") if session_engine is not None else None
        self._execution_broker = ToolBroker(
            self.workspace_root,
            {"read_file", "write_file", "patch_file", "run_process", "process_start", "process_poll", "process_write", "process_cancel"},
            constrained=False,
            process_root=process_root,
        )
        if session_engine is not None:
            session_engine.register_canceller(self._cancel_owned_processes)
        from smara.research_session import CanonicalResearchSession
        research_state_path=self.workspace_root/".smara"/"research"/f"agent-{uuid.uuid4().hex}.json" if session_engine is None else None
        self._research=CanonicalResearchSession(session_engine=session_engine,state_path=research_state_path)
        from smara.browser_session import CanonicalBrowserSession
        self._browser=CanonicalBrowserSession(self.workspace_root,session_engine=session_engine)

        self._tool_handlers = {
            "programmatic_tool_call": self._dispatch_programmatic_tool_call,
            "web_search": self._dispatch_web_search,
            "web_extract": self._dispatch_web_extract,
            "web_reader_dynamic": self._dispatch_web_reader_dynamic,
            "wayback_extract": self._dispatch_wayback_extract,
            "wikipedia_page": self._dispatch_wikipedia_page,
            "python_execute": self._dispatch_python_execute,
            "file_read": self._dispatch_file_read,
            "list_directory": self._dispatch_list_directory,
            "search_files": self._dispatch_search_files,
            "code_graph": self._dispatch_code_graph,
            "pdf_search": self._dispatch_pdf_search,
            "zip_extract_and_read": self._dispatch_zip_extract,
            "calculate": self._dispatch_calculate,
            "audio_transcribe": self._dispatch_audio_transcribe,
            "video_inspect": self._dispatch_video_inspect,
            "image_inspect": self._dispatch_image_inspect,
            "memory": self._dispatch_memory,
            "skills_list": self._dispatch_skills_list,
            "skill_view": self._dispatch_skill_view,
            "delegate_task": self._dispatch_delegate_task,
            "dag_flow": self._dispatch_dag_flow,
            "todo": self._dispatch_todo,
            "patch": self._dispatch_patch,
            "terminal": self._dispatch_terminal,
            "file_write": self._dispatch_file_write,
            "browser_action": self._dispatch_browser_action,
            "browser_open": self._dispatch_browser_open,
            "browser_observe": self._dispatch_browser_observe,
            "browser_navigate": self._dispatch_browser_navigate,
            "browser_act": self._dispatch_browser_act,
            "browser_tabs": self._dispatch_browser_tabs,
            "browser_switch": self._dispatch_browser_switch,
            "browser_scroll": self._dispatch_browser_scroll,
            "browser_download": self._dispatch_browser_download,
            "browser_close": self._dispatch_browser_close,
            "research_plan": self._dispatch_research_plan,
            "research_search": self._dispatch_research_search,
            "research_fetch": self._dispatch_research_fetch,
            "research_ingest_file": self._dispatch_research_ingest_file,
            "research_inspect": self._dispatch_research_inspect,
            "research_analyze": self._dispatch_research_analyze,
            "research_resolve": self._dispatch_research_resolve,
            "research_validate": self._dispatch_research_validate,
            "process_start": self._dispatch_process_start,
            "process_poll": self._dispatch_process_poll,
            "process_stdin": self._dispatch_process_stdin,
            "process_cancel": self._dispatch_process_cancel,
        }
        self._admitted_tool_names = {
            schema["function"]["name"] for schema in get_tool_schemas(self.toolset)
        }

    def _cancel_owned_processes(self):
        for process_id in list(self._execution_broker.processes.processes):
            with contextlib.suppress(Exception):self._execution_broker.processes.cancel(process_id)

    def _workspace_path(self, raw_path: str, *, allow_missing: bool = True) -> Path:
        """Resolve a legacy filesystem argument within this agent's workspace."""
        candidate = Path(raw_path) if raw_path else self.workspace_root
        resolved = (candidate if candidate.is_absolute() else self.workspace_root / candidate).resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("Path is outside the configured workspace.") from exc
        if not allow_missing and not resolved.exists():
            raise ValueError(f"Path does not exist: {raw_path}")
        return resolved

    def _report_progress(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.on_progress and callable(self.on_progress):
            try:
                self.on_progress(event_type, data)
            except Exception:
                pass

    def _dispatch_programmatic_tool_call(self, args: Dict[str, Any]) -> str:
        """Run a bounded batch through the same registered tool dispatcher.

        The kernel performs the security and size checks before invoking this
        agent's normal handlers, so a model cannot smuggle a shell command,
        mutation, credential access, or nested batch into one call.
        """
        calls = args.get("calls") if isinstance(args, dict) else None
        kernel = ProgrammaticToolKernel(self.execute_tool)
        return kernel.execute(calls).to_model_json()

    def _dispatch_todo(self, args: Dict[str, Any]) -> str:
        todos = args.get("todos")
        merge = args.get("merge", False)
        return todo_tool(todos=todos, merge=merge, planner=self.task_planner)

    def _dispatch_patch(self, args: Dict[str, Any]) -> str:
        from smara.harness import ToolCall
        path = args.get("path") or args.get("file_path") or ""
        old_string = args.get("old_string") or args.get("old_str") or ""
        new_string = args.get("new_string") or args.get("new_str") or ""
        replace_all = args.get("replace_all", False)
        result = self._execution_broker.dispatch(ToolCall(uuid.uuid4().hex, "patch_file", {"path": path, "old": old_string, "new": new_string, "replace_all": bool(replace_all)}, str(self.workspace_root)))
        return f"Patch applied successfully: {result.text}" if result.ok else f"Patch Error: {result.error_kind}: {result.text}"

    def _dispatch_terminal(self, args: Dict[str, Any]) -> str:
        from smara.harness import ToolCall
        cmd = args.get("command") or args.get("cmd") or ""
        timeout = args.get("timeout", 45)
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd] if sys.platform == "win32" else ["/bin/bash", "-c", cmd]
        scope = "full" if re.search(r"(?:^|\s)(?:pytest|npm\s+test|cargo\s+test|go\s+test)(?:\s|$)", cmd, re.I) else "none"
        result = self._execution_broker.dispatch(ToolCall(uuid.uuid4().hex, "run_process", {"argv": argv, "cwd": args.get("cwd") or ".", "timeout_seconds": timeout, "evidence_scope": scope}, str(self.workspace_root)))
        return f"[Exit Code: {result.exit_code}]\n{result.text}" if result.exit_code is not None else f"Error: {result.error_kind}: {result.text}"

    def _process_result(self,name,args):
        from smara.harness import ToolCall
        broker=self.session_engine.broker if self.session_engine is not None else self._execution_broker
        mapped="process_write" if name=="process_stdin" else name
        result=broker.dispatch(ToolCall(uuid.uuid4().hex,mapped,args,str(self.workspace_root)))
        return json.dumps({"status":result.status,"output":result.text,"exit_code":result.exit_code,"error_kind":result.error_kind,"meta":result.meta},sort_keys=True,default=str)

    def _dispatch_process_start(self,args):return self._process_result("process_start",args)
    def _dispatch_process_poll(self,args):return self._process_result("process_poll",args)
    def _dispatch_process_stdin(self,args):return self._process_result("process_stdin",args)
    def _dispatch_process_cancel(self,args):return self._process_result("process_cancel",args)

    def _dispatch_file_write(self, args: Dict[str, Any]) -> str:
        from smara.harness import ToolCall
        path = args.get("path") or args.get("file_path") or ""
        content = args.get("content", "")
        result = self._execution_broker.dispatch(ToolCall(uuid.uuid4().hex, "write_file", {"path": path, "content": content}, str(self.workspace_root)))
        if result.ok:
            return f"File successfully written: {result.text}"
        detail = "Path is outside the configured workspace." if result.error_kind == "policy_denied" else result.text
        return f"File Write Error: {result.error_kind}: {detail}"

    def _dispatch_browser_action(self, args: Dict[str, Any]) -> str:
        act = args.get("action", "scrape")
        url = args.get("url") or ""
        out_p = args.get("output_path")
        if act in {"scrape","screenshot","dom_snapshot"}:
            value=self._browser.open(url) if not self._browser.browser_session_id else self._browser.navigate(url) if url else self._browser.observe()
            return json.dumps(value,sort_keys=True,default=str)
        return json.dumps({"status":"error","reason":"unsupported legacy browser action; use typed managed-browser tools"},sort_keys=True)

    def _browser_result(self,value:Any) -> str:return json.dumps(value,sort_keys=True,default=str)
    def _dispatch_browser_open(self,args):return self._browser_result(self._browser.open(str(args.get("url") or "about:blank")))
    def _dispatch_browser_observe(self,args):return self._browser_result(self._browser.observe())
    def _dispatch_browser_navigate(self,args):return self._browser_result(self._browser.navigate(str(args.get("url") or "")))
    def _dispatch_browser_act(self,args):return self._browser_result(self._browser.act(str(args.get("observation_id") or ""),str(args.get("ref") or ""),str(args.get("action") or ""),args.get("value")))
    def _dispatch_browser_tabs(self,args):return self._browser_result(self._browser.tabs())
    def _dispatch_browser_switch(self,args):return self._browser_result(self._browser.switch(str(args.get("tab_id") or "")))
    def _dispatch_browser_scroll(self,args):return self._browser_result(self._browser.scroll(int(args.get("dy") or 600)))
    def _dispatch_browser_download(self,args):return self._browser_result(self._browser.download(str(args.get("observation_id") or ""),str(args.get("ref") or ""),str(args.get("destination") or "")))
    def _dispatch_browser_close(self,args):return self._browser_result(self._browser.close())

    def _dispatch_web_search(self, args: Dict[str, Any]) -> str:
        q = args.get("query") or args.get("q") or ""
        qs = args.get("queries")
        return web_search(query=q, queries=qs, max_results=args.get("max_results", 5))

    def _dispatch_web_extract(self, args: Dict[str, Any]) -> str:
        u = args.get("url") or ""
        us = args.get("urls")
        mc = args.get("max_chars", 5000)
        return web_extract(url=u, urls=us, max_chars=mc)

    def _research_result(self,value:Any) -> str:
        if self.session_engine is not None:self.session_engine.set("research_required",True)
        return json.dumps(value,sort_keys=True,default=str)

    def _dispatch_research_plan(self,args:Dict[str,Any]) -> str:
        return self._research_result(self._research.plan(args.get("question", ""),args.get("nodes") or []))

    def _dispatch_research_search(self,args:Dict[str,Any]) -> str:
        return self._research_result(self._research.search(str(args.get("node_id") or ""),str(args.get("query") or ""),int(args.get("max_results") or 5)))

    def _dispatch_research_fetch(self,args:Dict[str,Any]) -> str:
        return self._research_result(self._research.fetch(str(args.get("node_id") or ""),str(args.get("url") or "")))

    def _dispatch_research_ingest_file(self,args:Dict[str,Any]) -> str:
        return self._research_result(self._research.ingest_file(str(args.get("node_id") or ""),str(args.get("path") or ""),page=int(args.get("page") or 1),row=int(args.get("row") or 1),column=int(args.get("column") or 1)))

    def _dispatch_research_inspect(self,args:Dict[str,Any]) -> str:
        return self._research_result(self._research.inspect(str(args.get("evidence_id") or ""),int(args.get("max_chars") or 4000)))

    def _dispatch_research_analyze(self,args:Dict[str,Any]) -> str:
        return self._research_result(self._research.analyze(args.get("rows") or [],args.get("numeric_columns") or [],evidence_ids=args.get("evidence_ids") or [],group_by=args.get("group_by"),time_column=args.get("time_column")))

    def _dispatch_research_resolve(self,args:Dict[str,Any]) -> str:
        return self._research_result(self._research.resolve(str(args.get("node_id") or ""),str(args.get("claim") or ""),args.get("evidence_ids") or []))

    def _dispatch_research_validate(self,args:Dict[str,Any]) -> str:
        return self._research_result(self._research.validate(args.get("claims") or [],require_complete=bool(args.get("require_complete",True))))

    def _dispatch_web_reader_dynamic(self, args: Dict[str, Any]) -> str:
        u = args.get("url") or ""
        mc = args.get("max_chars", 16000)
        return web_reader_dynamic(u, max_chars=mc)

    def _dispatch_wayback_extract(self, args: Dict[str, Any]) -> str:
        u = args.get("url") or ""
        ts = args.get("timestamp") or args.get("date") or ""
        return wayback_extract(u, timestamp=ts)

    def _dispatch_wikipedia_page(self, args: Dict[str, Any]) -> str:
        t = args.get("title_or_url") or args.get("title") or args.get("url") or ""
        d = args.get("date_or_timestamp") or args.get("date") or args.get("timestamp") or ""
        a = args.get("action", "text")
        return wikipedia_page(t, date_or_timestamp=d, action=a)

    def _dispatch_python_execute(self, args: Dict[str, Any]) -> str:
        from smara.harness import ToolCall
        code = args.get("code") or args.get("script") or ""
        result = self._execution_broker.dispatch(ToolCall(uuid.uuid4().hex, "run_process", {"argv": [sys.executable, "-c", code], "cwd": ".", "timeout_seconds": 60, "evidence_scope": "none"}, str(self.workspace_root)))
        return f"[Exit Code: {result.exit_code}]\n{result.text}" if result.exit_code is not None else f"Python execution error: {result.error_kind}: {result.text}"

    def _dispatch_file_read(self, args: Dict[str, Any]) -> str:
        fp = args.get("file_path") or args.get("path") or ""
        offset = args.get("offset")
        limit = args.get("limit")
        max_chars = args.get("max_chars", 12000)
        try:
            admitted = self._execution_broker.path(fp)
        except Exception as exc:
            return f"File Read Error: policy_denied: {exc}"
        return file_read(str(admitted), offset=offset, limit=limit, max_chars=max_chars)

    def _dispatch_list_directory(self, args: Dict[str, Any]) -> str:
        p = args.get("path") or "."
        d = int(args.get("max_depth") or 2)
        return list_directory(str(self._workspace_path(p)), max_depth=d)

    def _dispatch_search_files(self, args: Dict[str, Any]) -> str:
        q = args.get("query") or ""
        p = args.get("path") or "."
        r = bool(args.get("is_regex", False))
        return search_files(q, path=str(self._workspace_path(p)), is_regex=r)

    def _dispatch_code_graph(self, args: Dict[str, Any]) -> str:
        op = args.get("operation") or "inspect_symbol"
        sym = args.get("symbol") or ""
        ws = str(self.workspace_root) if self.workspace_root else None
        return code_graph_tool(op, sym, workspace_root=ws)

    def _dispatch_pdf_search(self, args: Dict[str, Any]) -> str:
        p = args.get("pdf_path") or args.get("path") or ""
        q = args.get("query") or ""
        page = args.get("page")
        sp = page or args.get("start_page", 1)
        ep = page or args.get("end_page")
        mm = args.get("max_matches", 10)
        return pdf_search(p, query=q, start_page=sp, end_page=ep, page=page, max_matches=mm)

    def _dispatch_zip_extract(self, args: Dict[str, Any]) -> str:
        zp = args.get("zip_path") or ""
        tf = args.get("target_file")
        return zip_extract_and_read(zp, target_file=tf)

    def _dispatch_calculate(self, args: Dict[str, Any]) -> str:
        expr = args.get("expression") or args.get("expr") or ""
        return calculate(expr)

    def _dispatch_audio_transcribe(self, args: Dict[str, Any]) -> str:
        f = args.get("file_path_or_url") or args.get("file_path") or args.get("url") or ""
        return audio_transcribe(f)

    def _dispatch_video_inspect(self, args: Dict[str, Any]) -> str:
        u = args.get("url_or_path") or args.get("url") or ""
        act = args.get("action", "transcript")
        ts = args.get("timestamp_seconds")
        prompt = args.get("prompt")
        return video_inspect(u, action=act, timestamp_seconds=ts, prompt=prompt)

    def _dispatch_image_inspect(self, args: Dict[str, Any]) -> str:
        img = args.get("image_path") or args.get("path") or ""
        prompt = args.get("prompt") or "Describe this image in detail and transcribe all visible text."
        return image_inspect(img, prompt=prompt)

    def _dispatch_memory(self, args: Dict[str, Any]) -> str:
        return memory_tool(
            action=args.get("action", "list"),
            target=args.get("target", "memory"),
            content=args.get("content", ""),
            old_text=args.get("old_text", ""),
            query=args.get("query", "")
        )

    def _dispatch_skills_list(self, args: Dict[str, Any]) -> str:
        return skills_list_tool(tag_filter=args.get("tag_filter"))

    def _dispatch_skill_view(self, args: Dict[str, Any]) -> str:
        return skill_view_tool(
            skill_name=args.get("skill_name", ""),
            relative_path=args.get("relative_path")
        )

    def _dispatch_delegate_task(self, args: Dict[str, Any]) -> str:
        return delegate_task_tool(
            goal=args.get("goal", ""),
            context=args.get("context"),
            role=args.get("role", "generalist")
        )

    def _dispatch_dag_flow(self, args: Dict[str, Any]) -> str:
        return dag_flow_tool(
            action=args.get("action", "create_and_run"),
            workflow_data=args.get("workflow_data")
        )


    def execute_tool(self, tool_name: str, tool_args: Dict[str, Any], call_id: Optional[str] = None) -> str:
        """Safely invoke registered tool handler."""
        if tool_name in DISABLED_TOOLS:
            return f"Denied: Tool '{tool_name}' is disabled pending enforced delegation policy."
        if tool_name not in self._admitted_tool_names:
            return f"Denied: Tool '{tool_name}' is not admitted for profile '{self.toolset}'."
        if self.session_engine is not None and self.session_engine.broker.constrained and (tool_name.startswith("browser_") or tool_name.startswith("research_")) and tool_name not in self.session_engine.broker.grant:
            return f"Denied: Tool '{tool_name}' is not present in the durable session capability grant."
        handler = self._tool_handlers.get(tool_name)
        if not handler:
            return f"Error: Tool '{tool_name}' is not recognized. Available tools: {list(self._tool_handlers.keys())}"
        try:
            if self.session_engine is None:
                return handler(tool_args)
            from smara.harness import ToolCall, ToolResult, workspace_revision
            durable_id = call_id or f"tool_{uuid.uuid4().hex}"
            before = workspace_revision(self.workspace_root)
            def execute(raw: Dict[str, Any]) -> ToolResult:
                output = str(handler(tool_args)); after = workspace_revision(self.workspace_root)
                success = _tool_result_succeeded(output)
                exit_match = re.search(r"\[Exit Code:\s*(-?\d+)\]", output)
                exit_code = int(exit_match.group(1)) if exit_match else None
                scope = "none"
                if tool_name == "terminal" and re.search(r"(?:^|\s)(?:pytest|npm\s+test|cargo\s+test|go\s+test)(?:\s|$)", str(tool_args.get("command") or tool_args.get("cmd") or ""), re.I): scope = "full"
                meta={"evidence_scope":scope}
                if tool_name.startswith("process_"):
                    try:
                        process_result=json.loads(output)
                        meta.update(process_result.get("meta",{}))
                        exit_code=process_result.get("exit_code")
                        success = process_result.get("status") != "denied"
                    except Exception:
                        pass
                elif tool_name.startswith("browser_") or tool_name.startswith("research_"):
                    try:
                        json_result=json.loads(output)
                        if isinstance(json_result, dict):
                            success = json_result.get("status") != "error"
                    except Exception:
                        pass
                return ToolResult(durable_id,"ok" if success else "error",output,exit_code=exit_code,before_revision=before,after_revision=after,error_kind=None if success else "tool_error",meta=meta)
            result = self.session_engine.execute_incremental(ToolCall(durable_id,tool_name,tool_args,str(self.workspace_root)),execute)
            return result.text
        except Exception as e:
            logger.error(f"Error executing tool {tool_name} with args {tool_args}: {e}")
            return f"Error executing tool {tool_name}: {e}"

    def _call_model_api(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, max_tokens: int = 16384) -> Dict[str, Any]:
        """Perform HTTP POST request to OpenAI-compatible chat completions with Three-Zone Context Compaction."""
        from smara.context_packing import ContextOverflow, ModelContextProfile, pack_messages
        profile = ModelContextProfile(
            tokenizer_id=f"unknown:{self.model}",
            input_capacity=int(os.getenv("SMARA_MODEL_CONTEXT_TOKENS", "131072")),
            output_reserve=max_tokens,
        )
        compacted = _compact_conversation_history(
            messages,
            max_chars=int(os.getenv("SMARA_CONTEXT_MAX_CHARS", "45000")),
            planner=self.task_planner,
        )
        try:
            packed = pack_messages(compacted, profile, tools=tools or ())
        except ContextOverflow:
            emergency = _compact_conversation_history(compacted, max_chars=18000, planner=self.task_planner)
            for msg in emergency:
                c = str(msg.get("content") or "")
                if msg.get("role") != "system" and len(c) > 1000:
                    msg["content"] = c[:500] + f"\n... [Compacted {len(c)-800} chars] ...\n" + c[-300:]
            try:
                packed = pack_messages(emergency, profile, tools=tools or ())
            except ContextOverflow:
                profile_emergency = ModelContextProfile(
                    tokenizer_id=f"unknown:{self.model}",
                    input_capacity=int(os.getenv("SMARA_MODEL_CONTEXT_TOKENS", "131072")),
                    output_reserve=max(2048, min(max_tokens, 4096)),
                    safety_margin=64,
                )
                packed = pack_messages(emergency, profile_emergency, tools=tools or ())
        compacted_messages = list(packed.messages)
        self._report_progress("context_packed", {"input_tokens": packed.input_tokens, "accounting_quality": packed.accounting_quality, "omitted_messages": packed.omitted_messages})

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": compacted_messages,
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.auth_header == "api-subscription-key":
            headers["api-subscription-key"] = self.api_key
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        endpoint = self.base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint = f"{endpoint}/chat/completions"

        req = urllib.request.Request(
            endpoint,
            data=data,
            headers=headers
        )

        reservation_id = None
        if self.session_engine is not None:
            conservative_cost=max(0.000001,float(os.getenv("SMARA_MODEL_ESTIMATED_CALL_DOLLARS","0.01")))
            reservation_id = self.session_engine.reserve_model_call(packed.input_tokens + max_tokens,conservative_cost)
        def retry_wait(delay: float) -> None:
            if self.session_engine is None:
                time.sleep(delay);return
            deadline=time.monotonic()+delay
            while time.monotonic()<deadline:
                if self.session_engine is not None and self.session_engine.get("cancelled",False):
                    from smara.harness import BudgetExceeded
                    raise BudgetExceeded("cancelled")
                time.sleep(min(.05,max(0.0,deadline-time.monotonic())))
        retry_deadline = time.monotonic() + 180.0
        retries = 0
        for attempt in range(3):
            try:
                remaining = retry_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("model request retry deadline exhausted")
                with urllib.request.urlopen(req, timeout=min(90.0, remaining)) as resp:
                    response = json.loads(resp.read().decode("utf-8"))
                    if self.session_engine is not None:
                        if self.session_engine.get("cancelled",False):
                            from smara.harness import BudgetExceeded
                            raise BudgetExceeded("cancelled")
                        usage = response.get("usage") or {}; actual = usage.get("total_tokens")
                        self.session_engine.reconcile_model_call(reservation_id,actual_tokens=int(actual) if actual is not None else None,provider_request_id=resp.headers.get("x-request-id") if getattr(resp,"headers",None) else None,status="ok",retries=retries)
                    return response
            except urllib.error.HTTPError as he:
                err_msg = he.read().decode("utf-8", errors="ignore")
                logger.warning(f"Model API HTTPError (attempt {attempt+1}): {he.code} - {err_msg}")
                retries = attempt + 1
                # Credentials, permissions, and invalid requests are permanent
                # for this payload. Retrying them only burns budget and latency.
                if he.code in {400, 401, 403, 404, 409, 422} or attempt == 2:
                    if self.session_engine is not None: self.session_engine.reconcile_model_call(reservation_id,actual_tokens=None,provider_request_id=None,status=f"http_{he.code}",retries=retries)
                    raise RuntimeError(f"Model API HTTP {he.code}: {err_msg}")
                if he.code != 429 and he.code not in {408, 425, 500, 502, 503, 504}:
                    if self.session_engine is not None: self.session_engine.reconcile_model_call(reservation_id,actual_tokens=None,provider_request_id=None,status=f"http_{he.code}",retries=retries)
                    raise RuntimeError(f"Model API HTTP {he.code}: {err_msg}")
                retry_after = he.headers.get("Retry-After") if he.headers else None
                try: delay = max(0.0, min(float(retry_after), 10.0)) if retry_after is not None else min(0.5 * (2**attempt), 2.0)
                except (TypeError, ValueError): delay = min(0.5 * (2**attempt), 2.0)
                if time.monotonic() + delay >= retry_deadline:
                    if self.session_engine is not None: self.session_engine.reconcile_model_call(reservation_id,actual_tokens=None,provider_request_id=None,status="retry_deadline",retries=retries)
                    raise RuntimeError(f"Model API HTTP {he.code}: retry deadline exhausted")
                retry_wait(delay)
                if self.session_engine is not None:self.session_engine.reserve_model_retry(reservation_id)
            except Exception as e:
                logger.warning(f"Model API Request Error (attempt {attempt+1}): {e}")
                retries = attempt + 1
                if attempt == 2:
                    if self.session_engine is not None: self.session_engine.reconcile_model_call(reservation_id,actual_tokens=None,provider_request_id=None,status=type(e).__name__,retries=retries)
                    raise
                delay = min(0.5 * (2**attempt), 2.0)
                if time.monotonic() + delay >= retry_deadline:
                    raise
                retry_wait(delay)
                if self.session_engine is not None:self.session_engine.reserve_model_retry(reservation_id)

        raise RuntimeError("Model API: Max retries exceeded")

    _call_sarvam_api = _call_model_api

    def _build_dynamic_context(self) -> str:
        """Inject spatial and environment context into system prompt for real-world awareness."""
        cwd = self.workspace_root
        os_info = "Windows (PowerShell)" if sys.platform == "win32" else "Linux/Unix (Bash)"

        git_info = "Not a git repository"
        try:
            res = subprocess.run(["git", "branch", "--show-current"], cwd=str(cwd), capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                branch = res.stdout.strip() or "detached"
                res_status = subprocess.run(["git", "status", "--short"], cwd=str(cwd), capture_output=True, text=True, timeout=5)
                dirty = "clean" if not res_status.stdout.strip() else f"{len(res_status.stdout.strip().splitlines())} modified files"
                git_info = f"Branch: {branch} ({dirty})"
        except Exception:
            pass

        ignored = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "target", "build", "dist", ".gradle"}
        try:
            top_items = [p.name + ("/" if p.is_dir() else "") for p in sorted(cwd.iterdir()) if p.name not in ignored and not p.name.startswith(".pytest-")][:30]
            layout_str = ", ".join(top_items)
        except Exception:
            layout_str = "Unavailable"

        rules_section = ""
        try:
            from .workspace_rules import discover_workspace_rules, format_rules_for_prompt
            rules = discover_workspace_rules(cwd)
            if rules.get("found"):
                rules_section = format_rules_for_prompt(rules)
        except Exception:
            pass

        return (
            f"\n\n### Current Execution Environment:\n"
            f"- Host OS / Shell: {os_info}\n"
            f"- Workspace Root (cwd): {cwd}\n"
            f"- Git State: {git_info}\n"
            f"- Project Layout: {layout_str}\n"
            f"- Active Model: {self.model}\n"
            f"{rules_section}"
        )

    def run(
        self,
        task: str,
        file_path: Optional[str] = None,
        file_content: Optional[str] = None,
        max_iterations: Optional[int] = None,
        context_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Execute autonomous ReAct loop to solve the given task.
        """
        user_prompt = f"Task: {task}"
        if file_path:
            user_prompt += f"\nAssociated Task File: {file_path}"
        if file_content:
            user_prompt += f"\nFile Text Content Snippet:\n{file_content[:4000]}"

        # Render frozen memory snapshot for system prompt caching
        memory_snapshot = self.memory_store.render_frozen_snapshot()
        system_content = BASE_SYSTEM_PROMPT + self._build_dynamic_context()
        if memory_snapshot.strip():
            system_content += f"\n\n### Active Local Memory Snapshot:\n{memory_snapshot}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]
        if context_history:
            for item in context_history[-10:]:
                r = item.get("role")
                c = item.get("content")
                if r in ("user", "assistant") and c:
                    messages.append({"role": r, "content": c})

        messages.append({"role": "user", "content": user_prompt})
        if self.session_engine is not None:
            self.session_engine.begin_incremental(task)
            if self.toolset=="research":self.session_engine.set("research_required",True)
            saved_messages = self.session_engine.get("agent_messages")
            if isinstance(saved_messages, list) and saved_messages:
                messages = saved_messages
            else:
                self.session_engine.checkpoint(messages, {"phase": "model", "iteration": 0})

        trace: List[Dict[str, Any]] = []
        tools_used: List[str] = []
        final_answer = ""
        raw_concluding = ""
        consecutive_no_tool = 0

        logger.info(f"Starting autonomous ReAct agent for task: {task[:90]}...")

        # Maps a changed source path to its revision at the time it was last
        # changed.  A verification receipt is valid only after the most recent
        # mutation; command names and model prose are not evidence.
        pending_verification: dict[str, str | None] = {}
        verification_failed = False
        provider_budget_exhausted = False

        def _mark_mutation(args: Dict[str, Any]) -> None:
            nonlocal verification_failed
            path = str(args.get("path") or args.get("file_path") or "")
            if path and any(path.lower().endswith(ext) for ext in [".py", ".js", ".ts", ".rs", ".go", ".c", ".cpp", ".sh"]):
                try:
                    candidate = self._workspace_path(path)
                    revision = hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.exists() else None
                except Exception:
                    revision = None
                pending_verification[path] = revision
                # A subsequent edit creates a new candidate revision; failure
                # evidence for the old revision remains in the trace but does
                # not condemn a later repaired revision.
                verification_failed = False

        def _record_verification(tool: str, observation: str) -> None:
            nonlocal verification_failed
            if not pending_verification or tool not in {"terminal", "python_execute"}:
                return
            if not _tool_result_succeeded(observation):
                verification_failed = True
                return
            # A successful actual execution result verifies the current edit
            # revision, not an earlier state.  If a path cannot be read (for
            # example a test double), retain the pending state conservatively.
            for path, expected_revision in list(pending_verification.items()):
                try:
                    candidate = self._workspace_path(path)
                    current_revision = hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.exists() else None
                except Exception:
                    current_revision = None
                if current_revision == expected_revision and current_revision is not None:
                    pending_verification.pop(path, None)

        session_max = self.session_engine.budget.model_calls if self.session_engine and hasattr(self.session_engine, "budget") else None
        max_loop_iterations = min(max_iterations or self.max_iterations, session_max) if session_max is not None else (max_iterations or self.max_iterations)
        iteration = 0
        consecutive_planning_turns = 0
        while iteration < max_loop_iterations:
            iteration += 1

            logger.info(f"Agent Loop Iteration {iteration}/{max_loop_iterations}")

            # Keep tools available through the final iteration.  A budget limit
            # is not permission to manufacture a final answer.
            is_final_step = (iteration == max_loop_iterations)
            active_tools = None if consecutive_no_tool >= 3 else get_tool_schemas(self.toolset)

            try:
                resp = self._call_model_api(messages, tools=active_tools)
            except Exception as e:
                logger.error(f"Failed calling Model API: {e}")
                raw_concluding = f"API_ERROR: {e}"
                final_answer = ""
                provider_budget_exhausted = type(e).__name__ == "BudgetExceeded"
                break

            choice = resp.get("choices", [{}])[0]
            finish_reason = choice.get("finish_reason")
            msg = choice.get("message", {})
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            tool_calls = msg.get("tool_calls") or []

            if reasoning or content:
                self._report_progress("thought", {"iteration": iteration, "thought": reasoning or content})

            logger.info(f"Iter {iteration} resp: finish_reason={finish_reason}, content_len={len(content)}, reasoning_len={len(reasoning)}, tool_calls={len(tool_calls)}")
            if not tool_calls:
                logger.info(f"Iter {iteration} content: {repr(content[:150])}")
                logger.info(f"Iter {iteration} reasoning tail: {repr(reasoning[-200:])}")

            # Length Truncation Guard: if reasoning ran out of tokens before generating content or tool call
            if finish_reason == "length" and not content.strip() and not tool_calls:
                logger.warning(f"Iter {iteration}: Reasoning exceeded token budget. Prompting for concise output.")
                if is_final_step:
                    raw_concluding = reasoning.strip()
                    break
                messages.append({
                    "role": "user",
                    "content": "Notice: Reasoning stream limit reached. Please execute your next tool call or state your final verified answer on the final line as:\nFINAL ANSWER: <exact answer>"
                })
                continue

            # Repetition Guard: check for degenerate repeating loops in model reasoning or content
            if _is_repetition_dominated(reasoning) or _is_repetition_dominated(content):
                logger.warning(f"Iter {iteration}: Repetition loop detected in model output. Injecting guidance to halt repetition.")
                if is_final_step:
                    raw_concluding = (content.strip() or reasoning.strip())
                    trace.append({
                        "iteration": iteration,
                        "thought": reasoning or content,
                        "tool_name": None,
                        "tool_args": None,
                        "observation": "Halted repetition on final step"
                    })
                    break
                messages.append({
                    "role": "user",
                    "content": "Notice: Repetitive thinking pattern detected. Do not repeat previous thoughts. Synthesize your final answer from verified findings and output on the final line strictly as:\nFINAL ANSWER: <exact answer>"
                })
                continue

            # If tool calls were generated
            if tool_calls:
                clean_msg = dict(msg)
                clean_msg.setdefault("role", "assistant")
                if clean_msg.get("content") is None:
                    clean_msg["content"] = ""
                messages.append(clean_msg)

                parsed_calls = []
                for idx_tc, tc in enumerate(tool_calls):
                    fn_name = tc.get("function", {}).get("name", "")
                    raw_args = tc.get("function", {}).get("arguments", "{}")
                    call_id = tc.get("id", f"call_{iteration}_{idx_tc}")
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        parsed_args = {"query": str(raw_args)}
                    parsed_calls.append((tc, fn_name, parsed_args, call_id))

                def _execute_single_call(item):
                    tc, fn_name, parsed_args, call_id = item
                    # Stall Guard: track tool invocation signature
                    canonical_args = json.dumps(parsed_args, sort_keys=True, default=str)
                    sig = hashlib.sha256(f"{fn_name}:{canonical_args}".encode("utf-8")).hexdigest()[:16]
                    call_count = self._seen_tool_signatures[sig]
                    self._seen_tool_signatures[sig] += 1

                    stall_note = ""
                    if fn_name in IDEMPOTENT_TOOLS and call_count >= 2:
                        stall_note = f"[Stall Guard Notice: Tool '{fn_name}' has been called {call_count+1} times with identical arguments without advancing the state. Do not repeat this query. Try a different search angle or proceed to synthesize your answer from existing findings.]\n\n"

                    self._report_progress("tool_start", {"iteration": iteration, "tool": fn_name, "args": parsed_args})
                    logger.info(f"[Tool Call] {fn_name}({parsed_args})")
                    raw_obs = str(self.execute_tool(fn_name, parsed_args, call_id=call_id))
                    if fn_name in {"patch", "file_write"} and _tool_result_succeeded(raw_obs):
                        _mark_mutation(parsed_args)
                    _record_verification(fn_name, raw_obs)
                    guidance = ""
                    if fn_name == "research_resolve" and ("supported" in raw_obs or "ok" in raw_obs):
                        guidance = "\n[Research Guidance: Node resolved. Run research_validate on your resolved claims to verify evidence coverage, then deliver your concise answer with source URLs.]\n"
                    elif fn_name == "research_validate" and ("passed" in raw_obs or "validated" in raw_obs):
                        guidance = "\n[Research Guidance: Validation passed. Deliver your verified final answer now, cite public source URLs, and conclude with FINAL LABEL: supported.]\n"
                    obs = stall_note + _offload_massive_result(raw_obs, call_id=call_id) + guidance
                    self._report_progress("tool_end", {"iteration": iteration, "tool": fn_name, "observation": obs})
                    return tc, fn_name, parsed_args, call_id, obs

                results = [_execute_single_call(item) for item in parsed_calls]
                has_execution_tool = False
                has_planning_tool = False
                for tc, fn_name, parsed_args, call_id, obs in results:
                    tools_used.append(fn_name)
                    if fn_name in {"todo", "skills_list", "skill_view"}:
                        has_planning_tool = True
                    else:
                        has_execution_tool = True
                    trace.append({
                        "iteration": iteration,
                        "thought": reasoning or content,
                        "tool_name": fn_name,
                        "tool_args": parsed_args,
                        "observation": obs[:300] + ("..." if len(obs) > 300 else "")
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": str(obs)
                    })

                if has_planning_tool and not has_execution_tool:
                    consecutive_planning_turns += 1
                else:
                    consecutive_planning_turns = 0

                if consecutive_planning_turns >= 2:
                    messages.append({
                        "role": "user",
                        "content": "Notice: Task plan is active. Execute concrete tool actions (or state your verified final answer) directly rather than updating the plan repeatedly."
                    })

                consecutive_no_tool = 0
                if self.session_engine is not None:
                    self.session_engine.checkpoint(messages, {"phase": "model", "iteration": iteration, "tools_used": tools_used, "pending_verification": pending_verification})
                continue

            # Check if model formatted tool calls inside text or reasoning
            text_to_check = (content or "") + "\n" + (reasoning or "")
            text_calls = _extract_text_tool_calls(text_to_check)
            if text_calls:
                for fn_name, fn_args in text_calls:
                    self._report_progress("tool_start", {"iteration": iteration, "tool": fn_name, "args": fn_args})
                    logger.info(f"[Text Tool Call] {fn_name}({fn_args})")
                    text_call_id = f"text_{iteration}_{uuid.uuid4().hex[:12]}"
                    obs = str(self.execute_tool(fn_name, fn_args, call_id=text_call_id))
                    if fn_name in {"patch", "file_write"} and _tool_result_succeeded(obs):
                        _mark_mutation(fn_args)
                    _record_verification(fn_name, obs)
                    self._report_progress("tool_end", {"iteration": iteration, "tool": fn_name, "observation": obs})
                    tools_used.append(fn_name)
                    messages.append({"role": "assistant", "content": content or f"Tool call: {fn_name}"})
                    messages.append({
                        "role": "user",
                        "content": f"Tool '{fn_name}' returned:\n{obs}\n\nReview the observation carefully. If you now have the solution, provide your definitive answer on the final line strictly as:\nFINAL ANSWER: <exact answer>"
                    })
                    trace.append({
                        "iteration": iteration,
                        "thought": text_to_check,
                        "tool_name": fn_name,
                        "tool_args": fn_args,
                        "observation": obs[:300]
                    })
                consecutive_no_tool = 0
                continue

            # Check if model has provided the definitive final answer
            has_final_answer = False
            fa_pattern = r"(?:FINAL ANSWER|Final Answer|final answer|FINAL LABEL|Final Label|final label):\s*([^\n\r]+)"
            fa_match_c = re.search(fa_pattern, content or "")
            if fa_match_c:
                cand = fa_match_c.group(1).strip()
                if not _is_instruction_placeholder(cand):
                    has_final_answer = True

            if not has_final_answer and reasoning:
                fa_match_r = re.search(fa_pattern, reasoning)
                if fa_match_r:
                    cand = fa_match_r.group(1).strip()
                    if not _is_instruction_placeholder(cand):
                        has_final_answer = True
                        content = (content + "\n" if content.strip() else "") + f"FINAL ANSWER: {cand}"
                elif not content.strip() and not _is_instruction_placeholder(reasoning):
                    content = reasoning

            # Verification Gate: verify code modifications and calculations before confirming answer
            if has_final_answer:
                if self.session_engine is not None and self.session_engine.get("research_required",False):
                    research_ok,research_reason=self._research.can_finalize(content or reasoning)
                    if not research_ok:
                        logger.info("Research completion gate rejected final answer: %s",research_reason)
                        messages.append({"role":"assistant","content":content or reasoning})
                        messages.append({"role":"user","content":f"Research verification gate: {research_reason}. Resolve ready research nodes and call research_validate with every required claim and its fetched evidence before finalizing."})
                        trace.append({"iteration":iteration,"thought":reasoning or content,"tool_name":None,"tool_args":None,"observation":f"Research completion rejected: {research_reason}"})
                        continue
                if pending_verification:
                    unverified = list(pending_verification)
                    logger.info(f"Verification Gate: Prompting verification check for unverified code edits: {unverified}")
                    messages.append({"role": "assistant", "content": content or reasoning})
                    messages.append({
                        "role": "user",
                        "content": f"Verification check: You modified the following code file(s): {', '.join(unverified)}. Before confirming your final answer, execute a verification test (via 'python_execute' or test runner) to confirm the code runs without syntax errors or regressions."
                    })
                    trace.append({
                        "iteration": iteration,
                        "thought": reasoning or content,
                        "tool_name": None,
                        "tool_args": None,
                        "observation": f"Verification Gate: Prompted verification check for modified code files: {unverified}"
                    })
                    continue

                needs_calc = any(kw in task.lower() for kw in ["calculate", "standard deviation", "how many", "difference", "sum of", "volume", "speed"])
                if iteration == 1 and not tools_used and needs_calc:
                    logger.info("Verification Gate: Prompting single-pass calculation check before final answer acceptance.")
                    messages.append({"role": "assistant", "content": content or reasoning})
                    messages.append({
                        "role": "user",
                        "content": "Verification check: Before confirming your final answer, execute verification code via 'python_execute' or 'calculate' to confirm the exact numerical values and prevent calculation errors."
                    })
                    trace.append({
                        "iteration": iteration,
                        "thought": reasoning or content,
                        "tool_name": None,
                        "tool_args": None,
                        "observation": "Verification Gate: Prompted verification check before finalizing."
                    })
                    continue

            if has_final_answer:
                raw_concluding = (content.strip() or reasoning.strip())
                logger.info(f"Agent concluded in iteration {iteration}: {raw_concluding[:120]}...")
                trace.append({
                    "iteration": iteration,
                    "thought": reasoning or content,
                    "tool_name": None,
                    "tool_args": None,
                    "observation": "Final Answer Reached"
                })
                break

            # If no tool call and no FINAL ANSWER, the agent is thinking out loud.
            # Feed the thought back and prompt the agent to execute actions or state FINAL ANSWER.
            consecutive_no_tool += 1
            logger.info(f"Iteration {iteration}: Model responded without tool call or FINAL ANSWER (consecutive={consecutive_no_tool}). Prompting to proceed.")
            assistant_content = content.strip() or (f"Previous calculation: {reasoning[-400:]}" if reasoning else "Thinking...")
            messages.append({"role": "assistant", "content": assistant_content})

            if not tools_used:
                prompt_content = (
                    "Please take concrete action by calling one of the available tools (e.g. file_read, bash, list_directory, "
                    "search_files, python_execute, etc.) to inspect files, execute commands, or gather the required information. "
                    "If you already have the complete answer and no tool execution is required, provide your final response directly."
                )
            else:
                if self.session_engine is not None and self.session_engine.get("research_required", False):
                    prompt_content = (
                        "If you need to perform additional actions or verify, call the appropriate tool. "
                        "If you have completed the task and verified the result via research_validate, synthesize your final response. "
                        "Include the public source URL(s) or live dataset URL and end on the final line strictly with:\nFINAL LABEL: <supported|refuted|insufficient>"
                    )
                else:
                    prompt_content = (
                        "If you need to perform additional actions or verify, call the appropriate tool. "
                        "If you have completed the task and verified the result, synthesize your final response. "
                        "For benchmark evaluation tasks, output strictly on a single line as:\nFINAL ANSWER: <exact answer>"
                    )
            messages.append({
                "role": "user",
                "content": prompt_content
            })
            trace.append({
                "iteration": iteration,
                "thought": reasoning or content,
                "tool_name": None,
                "tool_args": None,
                "observation": f"Prompted agent to proceed (tools_used={bool(tools_used)})"
            })
            continue

        # Extract concise final answer
        if raw_concluding:
            if self.session_engine is not None and self.session_engine.get("research_required",False):
                final_answer = raw_concluding.strip()
                outcome = self._research.primary_outcome() if hasattr(self, "_research") else ""
                if outcome and not re.search(rf"\b{re.escape(outcome)}\b", final_answer, re.IGNORECASE):
                    final_answer = f"{final_answer}\nFINAL LABEL: {outcome}"
            else:
                final_answer = self._clean_final_answer(raw_concluding)
        
        status = "completed"
        if provider_budget_exhausted:
            status = "budget_exhausted"
        elif verification_failed:
            status = "tool_error"
        elif pending_verification:
            status = "budget_exhausted" if iteration >= max_loop_iterations else "unverified"
        elif not final_answer:
            status = "budget_exhausted" if iteration >= max_loop_iterations else "incomplete"

        self._report_progress("answer", {
            "answer": final_answer,
            "raw_answer": raw_concluding,
            "iterations": iteration,
            "tools_used": list(dict.fromkeys(tools_used)),
            "status": status,
        })

        session_result = None
        if self.session_engine is not None:
            unresolved = ["provider/model budget exhausted"] if status == "budget_exhausted" else []
            if hasattr(self, "_research") and self.session_engine.get("research_required", False):
                self.session_engine.set("research_outcome", self._research.primary_outcome())
            session_result = self.session_engine.finish_incremental(status if status in {"completed","budget_exhausted","tool_error"} else "needs_input", final_answer, unresolved)
            if hasattr(self, "_research") and self.session_engine.get("research_required", False):
                session_result["research_outcome"] = self._research.primary_outcome()
            status = session_result["status"]
        return {
            "answer": final_answer,
            "raw_answer": raw_concluding,
            "trace": trace,
            "tools_used": list(dict.fromkeys(tools_used)),
            "iterations": iteration,
            "status": status,
            "completed": status == "completed",
            "session": session_result,
        }

    @staticmethod
    def _clean_final_answer(text: str) -> str:
        """Extract exact concise answer adhering to standard question-answering formatting."""
        if not text:
            return ""

        # 1. Primary explicit prefix
        fa_match = re.search(r"(?:FINAL ANSWER|Final Answer|final answer|Answer):\s*([^\n\r]+)", text, re.IGNORECASE)
        cand_fa = fa_match.group(1).strip() if fa_match else ""
        if cand_fa and not _is_instruction_placeholder(cand_fa):
            ans = cand_fa
        else:
            # 2. Strong concluding phrases
            ans_match = re.search(r"(?:the answer is|the result is|the value is|the percentage is|percentage is|therefore,?\s*(?:the answer is)?)\s*([^.\n\r]+)", text, re.IGNORECASE)
            cand_alt = ans_match.group(1).strip() if ans_match else ""
            if cand_alt and not _is_instruction_placeholder(cand_alt):
                ans = cand_alt
            else:
                # 3. Dual-stream mathematical percentage/ratio equations e.g. "180/5 = 36.0", "6/7 = 85.7% -> 86%", "equals 86%", "is 86%"
                math_pct = re.findall(r"(?:=\s*|is\s*|equals?\s*|percentage is\s*)([0-9]+(?:\.[0-9]+)?)\s*%", text, re.IGNORECASE)
                if math_pct:
                    ans = math_pct[-1]
                else:
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    ans = ""
                    for l in reversed(lines):
                        if not _is_instruction_placeholder(l) and not l.startswith("###") and not l.startswith("==="):
                            ans = l
                            break

        ans = re.sub(
            r"^(?:FINAL ANSWER|Final Answer|final answer|Answer|The answer is|The result is|It is|Output:?)\s*[:\-]?\s*",
            "",
            ans,
            flags=re.IGNORECASE
        )
        ans = ans.strip("`*\"'").strip()

        # If answer has trailing parenthetical explanation, e.g. "42 (computed by...)", keep primary answer
        paren_m = re.match(r"^([^\(\)]+?)\s*\([^\)]*\)$", ans)
        if paren_m and paren_m.group(1).strip():
            ans = paren_m.group(1).strip()

        # Strip trailing explanation phrases like 'based on the calculation'
        ans = re.split(r"\s+(?:based on|according to|from the)\b", ans, flags=re.IGNORECASE)[0].strip()

        # Strip currency symbols if followed by digits
        if ans.startswith("$") and len(ans) > 1 and ans[1].isdigit():
            ans = ans[1:].strip()

        # If integer with commas (e.g. "1,234,567"), strip commas
        if re.match(r"^\d{1,3}(?:,\d{3})+$", ans):
            ans = ans.replace(",", "")

        # If answer is a number followed by unit text (e.g. "42.5 kg" or "100 meters"), keep the bare number
        num_unit = re.match(
            r"^([+-]?\d+(?:\.\d+)?)\s*(?:[a-zA-Z%°]+)\b",
            ans,
            flags=re.IGNORECASE
        )
        if num_unit:
            ans = num_unit.group(1)
        # Strip outer formatting, preserving period for multi-word sentence answers
        ans = ans.strip("`*\"' ")
        if not (len(ans.split()) > 2 and ans.endswith(".")):
            ans = ans.rstrip(".")
        ans = ans.strip("`*\"' ")
        if _is_instruction_placeholder(ans):
            return ""
        return ans

    solve = run
