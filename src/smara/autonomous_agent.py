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
import concurrent.futures
import hashlib
import sys
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
)
from smara.task_memory import get_default_memory_store
from smara.task_planner import SmaraTaskPlanner

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


def _compact_conversation_history(
    messages: List[Dict[str, Any]],
    max_chars: int = 35000,
    planner: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Three-Zone Context Compactor:
    Zone 1: Pinned Head (system prompt and original user task)
    Zone 2: Pinned Tail (most recent 4 messages)
    Zone 3: Middle Turns (compact large observations to preserve attention and token budget)
    Also preserves active task checklist from SmaraTaskPlanner across compaction.
    """
    total_chars = sum(len(str(m.get("content") or "")) for m in messages)
    if total_chars <= max_chars or len(messages) <= 6:
        return messages

    head_count = 2
    tail_count = min(4, len(messages) - head_count)
    middle_messages = messages[head_count : len(messages) - tail_count]

    compacted_middle: List[Dict[str, Any]] = []
    for m in middle_messages:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "tool" and len(content) > 800:
            compacted_m = dict(m)
            compacted_m["content"] = content[:350] + f"\n... [Context Compaction: {len(content)-550} chars omitted to preserve attention budget] ...\n" + content[-200:]
            compacted_middle.append(compacted_m)
        elif role == "assistant" and len(content) > 1200:
            compacted_m = dict(m)
            compacted_m["content"] = content[:600] + "\n... [Assistant thought condensed] ...\n" + content[-300:]
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

    return messages[:head_count] + compacted_middle + messages[len(messages) - tail_count:]


def _offload_massive_result(content: str, call_id: str, max_chars: int = 14000) -> str:
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
    """Detect if string is a prompt instruction placeholder rather than a genuine answer."""
    if not text:
        return True
    t = text.strip().lower()
    t_clean = re.sub(r"^[\<\\[\(\"']+|[\>\\]\)\"']+$", "", t).strip()
    placeholders = {
        "exact answer", "answer", "final answer", "your answer",
        "insert answer here", "insert answer", "value", "exact answer here",
        "result", "exact result", "undefined", "n/a", "none"
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
            "name": "web_search",
            "description": "Search the live web using Tavily / Google / DuckDuckGo for factual, up-to-date, or historical information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query keywords."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of search results to return (default 5).",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_extract",
            "description": "Fetch and parse full readable text content from a web URL or Wikipedia article.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full HTTP or HTTPS URL to fetch."
                    }
                },
                "required": ["url"]
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
            "description": "Read contents of a local file (.txt, .json, .csv, .py, .md, .html, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file to inspect."
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (default 8000).",
                        "default": 8000
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
    }
]


def get_tool_schemas(profile: str = "full") -> List[Dict[str, Any]]:
    """Return tool schemas filtered by profile to optimize token budget."""
    prof = (profile or "full").lower().strip()
    if prof == "full":
        return TOOL_SCHEMAS
    elif prof in ["coding", "swe"]:
        allowed = {"terminal", "file_write", "patch", "python_execute", "file_read", "todo", "delegate_task", "dag_flow"}
    elif prof in ["research", "web"]:
        allowed = {"browser_action", "web_search", "web_extract", "web_reader_dynamic", "wayback_extract", "wikipedia_page", "pdf_search", "calculate", "file_read", "todo"}
    elif prof in ["multimodal", "vision", "audio"]:
        allowed = {"browser_action", "image_inspect", "audio_transcribe", "video_inspect", "file_read", "todo"}
    else:
        return TOOL_SCHEMAS
    return [s for s in TOOL_SCHEMAS if s.get("function", {}).get("name") in allowed]


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
   - For factual web research, use `web_search` and `web_extract`.
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


3. **Honesty and Verification**:
   - Never fabricate or guess facts, URLs, dates, or calculations.
   - Verify every intermediate step with real tool outputs.

4. **Strict Final Answer Delivery Format**:
   - When verified, provide your definitive answer on the final line strictly as:
     FINAL ANSWER: <exact answer>
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
    """Autonomous ReAct agent interacting with Sarvam LLM via multi-turn tool-calling loops."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.sarvam.ai/v2/chat/completions",
        model: str = "glm5.2",
        max_iterations: int = 16,
        toolset: str = "full",
    ):
        self.api_key = api_key or _get_api_key_from_vault_or_env()
        self.base_url = base_url
        self.model = model
        self.max_iterations = max_iterations
        self.toolset = toolset
        self.task_planner = SmaraTaskPlanner()
        self.memory_store = get_default_memory_store()
        self._seen_tool_signatures: Dict[str, int] = collections.defaultdict(int)

        self._tool_handlers = {
            "web_search": self._dispatch_web_search,
            "web_extract": self._dispatch_web_extract,
            "web_reader_dynamic": self._dispatch_web_reader_dynamic,
            "wayback_extract": self._dispatch_wayback_extract,
            "wikipedia_page": self._dispatch_wikipedia_page,
            "python_execute": self._dispatch_python_execute,
            "file_read": self._dispatch_file_read,
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
        }

    def _dispatch_todo(self, args: Dict[str, Any]) -> str:
        todos = args.get("todos")
        merge = args.get("merge", False)
        return todo_tool(todos=todos, merge=merge, planner=self.task_planner)

    def _dispatch_patch(self, args: Dict[str, Any]) -> str:
        path = args.get("path") or args.get("file_path") or ""
        old_string = args.get("old_string") or args.get("old_str") or ""
        new_string = args.get("new_string") or args.get("new_str") or ""
        replace_all = args.get("replace_all", False)
        return patch_file_tool(path=path, old_string=old_string, new_string=new_string, replace_all=replace_all)

    def _dispatch_terminal(self, args: Dict[str, Any]) -> str:
        cmd = args.get("command") or args.get("cmd") or ""
        cwd = args.get("cwd")
        timeout = args.get("timeout", 45)
        return terminal_execute(command=cmd, cwd=cwd, timeout=timeout)

    def _dispatch_file_write(self, args: Dict[str, Any]) -> str:
        path = args.get("path") or args.get("file_path") or ""
        content = args.get("content", "")
        return file_write(path=path, content=content)

    def _dispatch_browser_action(self, args: Dict[str, Any]) -> str:
        act = args.get("action", "scrape")
        url = args.get("url") or ""
        out_p = args.get("output_path")
        return browser_action_tool(action=act, url=url, output_path=out_p)

    def _dispatch_web_search(self, args: Dict[str, Any]) -> str:
        q = args.get("query") or args.get("q") or ""
        return web_search(q, max_results=args.get("max_results", 5))

    def _dispatch_web_extract(self, args: Dict[str, Any]) -> str:
        u = args.get("url") or ""
        return web_extract(u)

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
        code = args.get("code") or args.get("script") or ""
        return python_execute(code)

    def _dispatch_file_read(self, args: Dict[str, Any]) -> str:
        fp = args.get("file_path") or args.get("path") or ""
        return file_read(fp, max_chars=args.get("max_chars", 8000))

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


    def execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Safely invoke registered tool handler."""
        handler = self._tool_handlers.get(tool_name)
        if not handler:
            return f"Error: Tool '{tool_name}' is not recognized. Available tools: {list(self._tool_handlers.keys())}"
        try:
            return handler(tool_args)
        except Exception as e:
            logger.error(f"Error executing tool {tool_name} with args {tool_args}: {e}")
            return f"Error executing tool {tool_name}: {e}"

    def _call_sarvam_api(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Perform HTTP POST request to Sarvam /v2/chat/completions with Three-Zone Context Compaction."""
        # Active Three-Zone Context Compaction: protects Head/Tail, compresses middle steps, preserves active todos
        compacted_messages = _compact_conversation_history(messages, max_chars=35000, planner=self.task_planner)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": compacted_messages,
            "temperature": 0.0,
            "max_tokens": 8192,
        }
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "api-subscription-key": self.api_key,
            }
        )

        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as he:
                err_msg = he.read().decode("utf-8", errors="ignore")
                logger.warning(f"Sarvam HTTPError (attempt {attempt+1}): {he.code} - {err_msg}")
                if attempt == 2:
                    raise RuntimeError(f"Sarvam API HTTP {he.code}: {err_msg}")
                time.sleep(2)
            except Exception as e:
                logger.warning(f"Sarvam Request Error (attempt {attempt+1}): {e}")
                if attempt == 2:
                    raise
                time.sleep(2)

        raise RuntimeError("Sarvam API: Max retries exceeded")

    def run(
        self,
        task: str,
        file_path: Optional[str] = None,
        file_content: Optional[str] = None,
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
        system_content = BASE_SYSTEM_PROMPT
        if memory_snapshot.strip():
            system_content += f"\n\n### Active Local Memory Snapshot:\n{memory_snapshot}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_prompt}
        ]

        trace: List[Dict[str, Any]] = []
        tools_used: List[str] = []
        final_answer = ""
        raw_concluding = ""
        consecutive_no_tool = 0

        logger.info(f"Starting autonomous ReAct agent for task: {task[:90]}...")

        touched_code_files: set[str] = set()

        for iteration in range(1, self.max_iterations + 1):
            logger.info(f"Agent Loop Iteration {iteration}/{self.max_iterations}")

            # If agent has already observed tool outputs or reached final iteration, disable tools to force clean synthesis
            is_final_step = (iteration == self.max_iterations)
            active_tools = None if (is_final_step or consecutive_no_tool >= 1) else get_tool_schemas(self.toolset)

            if is_final_step:
                messages.append({
                    "role": "user",
                    "content": "You have reached the final step. Synthesize all observations above and state your definitive FINAL ANSWER immediately without calling further tools."
                })

            try:
                resp = self._call_sarvam_api(messages, tools=active_tools)
            except Exception as e:
                logger.error(f"Failed calling Sarvam API: {e}")
                raw_concluding = f"API_ERROR: {e}"
                final_answer = ""
                break

            choice = resp.get("choices", [{}])[0]
            finish_reason = choice.get("finish_reason")
            msg = choice.get("message", {})
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            tool_calls = msg.get("tool_calls") or []

            logger.info(f"Iter {iteration} resp: finish_reason={finish_reason}, content_len={len(content)}, reasoning_len={len(reasoning)}, tool_calls={len(tool_calls)}")
            if not tool_calls:
                logger.info(f"Iter {iteration} content: {repr(content[:150])}")
                logger.info(f"Iter {iteration} reasoning tail: {repr(reasoning[-200:])}")

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

                    if fn_name in ["patch", "file_write"]:
                        p = str(parsed_args.get("path") or "")
                        if p and any(p.lower().endswith(ext) for ext in [".py", ".js", ".ts", ".rs", ".go", ".c", ".cpp", ".sh"]):
                            touched_code_files.add(p)
                    elif fn_name in ["python_execute"]:
                        touched_code_files.clear()
                    elif fn_name == "terminal":
                        cmd_str = str(parsed_args.get("command") or "").lower()
                        if any(kw in cmd_str for kw in ["pytest", "test", "check", "cargo test", "npm test", "go test", "python -m pytest"]):
                            touched_code_files.clear()

                    logger.info(f"[Tool Call] {fn_name}({parsed_args})")
                    raw_obs = str(self.execute_tool(fn_name, parsed_args))
                    # Spill safety: offload massive results to disk cache
                    obs = stall_note + _offload_massive_result(raw_obs, call_id=call_id)
                    return tc, fn_name, parsed_args, call_id, obs

                # Concurrent dispatch when multiple tool calls are emitted in one turn
                if len(parsed_calls) > 1:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(parsed_calls), 4)) as pool:
                        results = list(pool.map(_execute_single_call, parsed_calls))
                else:
                    results = [_execute_single_call(parsed_calls[0])]

                for tc, fn_name, parsed_args, call_id, obs in results:
                    tools_used.append(fn_name)
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

                consecutive_no_tool = 0
                continue

            # Check if model formatted tool calls inside text or reasoning
            text_to_check = (content or "") + "\n" + (reasoning or "")
            xml_match = re.search(r"<tool_call>\s*({.*?})\s*</tool_call>", text_to_check, re.DOTALL)
            if xml_match:
                try:
                    call_obj = json.loads(xml_match.group(1))
                    fn_name = call_obj.get("name")
                    fn_args = call_obj.get("arguments", {})
                    obs = str(self.execute_tool(fn_name, fn_args))
                    tools_used.append(fn_name)
                    if fn_name in ["patch", "file_write"]:
                        p = str(fn_args.get("path") or "")
                        if p and any(p.lower().endswith(ext) for ext in [".py", ".js", ".ts", ".rs", ".go", ".c", ".cpp", ".sh"]):
                            touched_code_files.add(p)
                    elif fn_name in ["python_execute"]:
                        touched_code_files.clear()
                    elif fn_name == "terminal":
                        cmd_str = str(fn_args.get("command") or "").lower()
                        if any(kw in cmd_str for kw in ["pytest", "test", "check", "cargo test", "npm test", "go test", "python -m pytest"]):
                            touched_code_files.clear()
                    messages.append({"role": "assistant", "content": content or f"Tool call: {fn_name}"})
                    messages.append({
                        "role": "user",
                        "content": f"Tool '{fn_name}' returned:\n{obs}\n\nReview the observation carefully. If you now have the solution, provide your definitive answer on the final line strictly as:\nFINAL ANSWER: <exact answer>\n(Note: When quoting or returning a decrypted string or code result, copy the exact characters and punctuation from the tool output verbatim without altering any spelling)."
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
                except Exception as ex:
                    logger.warning(f"Error parsing text tool call: {ex}")

            # Check if model has provided the definitive final answer
            has_final_answer = False
            fa_pattern = r"(?:FINAL ANSWER|Final Answer|final answer):\s*([^\n\r]+)"
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
            if has_final_answer and not is_final_step:
                if touched_code_files:
                    unverified = list(touched_code_files)
                    touched_code_files.clear()
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

            if has_final_answer or is_final_step:
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
            messages.append({
                "role": "user",
                "content": "You have gathered the necessary observations. Synthesize your final result and output strictly on a single line as:\nFINAL ANSWER: <exact answer>"
            })
            trace.append({
                "iteration": iteration,
                "thought": reasoning or content,
                "tool_name": None,
                "tool_args": None,
                "observation": "Prompted agent to synthesize final answer"
            })
            continue

        # Extract concise final answer
        if raw_concluding:
            final_answer = self._clean_final_answer(raw_concluding)
        
        if not final_answer and trace:
            for step in reversed(trace):
                cand_thought = step.get("thought") or ""
                if cand_thought:
                    fa_cand = self._clean_final_answer(cand_thought)
                    if fa_cand and not _is_instruction_placeholder(fa_cand):
                        final_answer = fa_cand
                        break
                cand_obs = step.get("observation") or ""
                if cand_obs and not cand_obs.startswith("Verification") and not cand_obs.startswith("Prompted"):
                    fa_cand = self._clean_final_answer(cand_obs)
                    if fa_cand and not _is_instruction_placeholder(fa_cand):
                        final_answer = fa_cand
                        break

        return {
            "answer": final_answer,
            "raw_answer": raw_concluding,
            "trace": trace,
            "tools_used": list(dict.fromkeys(tools_used)),
            "iterations": iteration
        }

    @staticmethod
    def _clean_final_answer(text: str) -> str:
        """Extract exact concise answer adhering to standard question-answering formatting."""
        if not text:
            return ""

        fa_match = re.search(r"(?:FINAL ANSWER|Final Answer|final answer|Answer):\s*([^\n\r]+)", text, re.IGNORECASE)
        cand_fa = fa_match.group(1).strip() if fa_match else ""
        if cand_fa and not _is_instruction_placeholder(cand_fa):
            ans = cand_fa
        else:
            ans_match = re.search(r"(?:the answer is|the result is|the value is|therefore,?\s*(?:the answer is)?)\s*([^.\n\r]+)", text, re.IGNORECASE)
            cand_alt = ans_match.group(1).strip() if ans_match else ""
            if cand_alt and not _is_instruction_placeholder(cand_alt):
                ans = cand_alt
            else:
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                ans = ""
                for l in reversed(lines):
                    if not _is_instruction_placeholder(l):
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

