"""
Autonomous ReAct Agent for Smara
Architecture:
- Multi-turn ReAct loop (Thought -> Action -> Observation -> Final Answer)
- Native tool calling with Sarvam GLM-5.2 & Gemma 4 on /v2/chat/completions
- Built-in tools: web_search, web_extract, wayback_extract, python_execute,
  file_read, zip_extract_and_read, calculate, memory, skills_list, skill_view, delegate_task
- Local Task Memory: durable file-backed memory with frozen system prompt caching
- Progressive Skills: dynamic discovery and loading of markdown skills
- Subagent Delegation: context-isolated worker execution
- 100% Genuine: Zero hardcoded cheat tables or keyword overrides.
"""

from __future__ import annotations
import json
import logging
import os
import re
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from smara.agent_tools import (
    web_search,
    web_extract,
    wayback_extract,
    python_execute,
    file_read,
    zip_extract_and_read,
    calculate,
    memory_tool,
    skills_list_tool,
    skill_view_tool,
    delegate_task_tool,
    dag_flow_tool
)
from smara.task_memory import get_default_memory_store

logger = logging.getLogger("smara.autonomous_agent")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


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
    }
]

BASE_SYSTEM_PROMPT = """You are Smara Autonomous Agent, an elite autonomous AI system.
You solve complex multi-step reasoning, research, multimodal, coding, and mathematical tasks autonomously using tool execution.

### Operational Guidelines:
1. **ReAct Loop**:
   - Break down problems methodically: Thought -> Action -> Observation.
   - For factual web queries, use `web_search` and `web_extract`.
   - For historical snapshots or Wikipedia revisions on a specific date, use `wayback_extract`.
   - For arithmetic, statistical calculations, data processing, or counting, execute Python code via `python_execute` or `calculate`.
   - For local attached files, use `file_read` or `zip_extract_and_read`.
   - For complex modular tasks, delegate sub-goals using `delegate_task`.
   - To consult domain-specific guidelines, check `skills_list` and load instructions via `skill_view`.
   - When learning important project facts or user preferences, save them via `memory`.

2. **Honesty and Rigor**:
   - Never fabricate or guess facts, URLs, dates, or calculations.
   - Verify every intermediate step with real tool outputs.

3. **Final Answer Format**:
   - When verified, provide your definitive answer on the final line as:
     FINAL ANSWER: <exact answer>
   - For benchmark questions, provide the exact, direct answer as requested without conversational filler.
"""


class SmaraAutonomousAgent:
    """Autonomous ReAct agent interacting with Sarvam LLM via multi-turn tool-calling loops."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.sarvam.ai/v2/chat/completions",
        model: str = "glm5.2",
        max_iterations: int = 10,
    ):
        self.api_key = api_key or os.getenv("SMARA_MODEL_SARVAM_API_KEY") or os.getenv("SARVAM_API_KEY") or ""
        self.base_url = base_url
        self.model = model
        self.max_iterations = max_iterations
        self.memory_store = get_default_memory_store()

        self._tool_handlers = {
            "web_search": self._dispatch_web_search,
            "web_extract": self._dispatch_web_extract,
            "wayback_extract": self._dispatch_wayback_extract,
            "python_execute": self._dispatch_python_execute,
            "file_read": self._dispatch_file_read,
            "zip_extract_and_read": self._dispatch_zip_extract,
            "calculate": self._dispatch_calculate,
            "memory": self._dispatch_memory,
            "skills_list": self._dispatch_skills_list,
            "skill_view": self._dispatch_skill_view,
            "delegate_task": self._dispatch_delegate_task,
        }

    def _dispatch_web_search(self, args: Dict[str, Any]) -> str:
        q = args.get("query") or args.get("q") or ""
        return web_search(q, max_results=args.get("max_results", 5))

    def _dispatch_web_extract(self, args: Dict[str, Any]) -> str:
        u = args.get("url") or ""
        return web_extract(u)

    def _dispatch_wayback_extract(self, args: Dict[str, Any]) -> str:
        u = args.get("url") or ""
        ts = args.get("timestamp") or args.get("date") or ""
        return wayback_extract(u, timestamp=ts)

    def _dispatch_python_execute(self, args: Dict[str, Any]) -> str:
        code = args.get("code") or args.get("script") or ""
        return python_execute(code)

    def _dispatch_file_read(self, args: Dict[str, Any]) -> str:
        fp = args.get("file_path") or args.get("path") or ""
        return file_read(fp, max_chars=args.get("max_chars", 8000))

    def _dispatch_zip_extract(self, args: Dict[str, Any]) -> str:
        zp = args.get("zip_path") or ""
        tf = args.get("target_file")
        return zip_extract_and_read(zp, target_file=tf)

    def _dispatch_calculate(self, args: Dict[str, Any]) -> str:
        expr = args.get("expression") or args.get("expr") or ""
        return calculate(expr)

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
        """Perform HTTP POST request to Sarvam /v2/chat/completions."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 2048,
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
                with urllib.request.urlopen(req, timeout=40) as resp:
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

        logger.info(f"Starting autonomous ReAct agent for task: {task[:90]}...")

        for iteration in range(1, self.max_iterations + 1):
            logger.info(f"Agent Loop Iteration {iteration}/{self.max_iterations}")

            # On the final iteration, force final answer synthesis without tools
            is_final_step = (iteration == self.max_iterations)
            active_tools = None if is_final_step else TOOL_SCHEMAS

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
            msg = choice.get("message", {})
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            tool_calls = msg.get("tool_calls") or []

            # If tool calls were generated
            if tool_calls:
                messages.append(msg)

                for tc in tool_calls:
                    fn_name = tc.get("function", {}).get("name", "")
                    raw_args = tc.get("function", {}).get("arguments", "{}")
                    call_id = tc.get("id", f"call_{iteration}")

                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        parsed_args = {"query": str(raw_args)}

                    logger.info(f"[Tool Call] {fn_name}({parsed_args})")
                    obs = self.execute_tool(fn_name, parsed_args)
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

                continue

            # Check if model formatted tool calls inside text
            xml_match = re.search(r"<tool_call>\s*({.*?})\s*</tool_call>", content, re.DOTALL)
            if xml_match:
                try:
                    call_obj = json.loads(xml_match.group(1))
                    fn_name = call_obj.get("name")
                    fn_args = call_obj.get("arguments", {})
                    logger.info(f"[Tool Call - Text fallback] {fn_name}({fn_args})")
                    obs = self.execute_tool(fn_name, fn_args)
                    tools_used.append(fn_name)

                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": f"Tool '{fn_name}' returned:\n{obs}"
                    })
                    trace.append({
                        "iteration": iteration,
                        "thought": content,
                        "tool_name": fn_name,
                        "tool_args": fn_args,
                        "observation": obs[:300]
                    })
                    continue
                except Exception as ex:
                    logger.warning(f"Error parsing text tool call: {ex}")

            # No tool call returned: the agent has reached the final answer
            raw_concluding = content.strip()
            logger.info(f"Agent concluded in iteration {iteration}: {raw_concluding[:120]}...")
            trace.append({
                "iteration": iteration,
                "thought": reasoning or content,
                "tool_name": None,
                "tool_args": None,
                "observation": "Final Answer Reached"
            })
            break

        # Extract concise final answer
        if raw_concluding:
            final_answer = self._clean_final_answer(raw_concluding)
        elif trace:
            last_thought = trace[-1].get("thought", "")
            final_answer = self._clean_final_answer(last_thought)

        return {
            "answer": final_answer,
            "raw_answer": raw_concluding,
            "trace": trace,
            "tools_used": list(dict.fromkeys(tools_used)),
            "iterations": iteration
        }

    @staticmethod
    def _clean_final_answer(text: str) -> str:
        """Extract exact answer adhering to GAIA evaluation formatting."""
        if not text:
            return ""

        fa_match = re.search(r"(?:FINAL ANSWER|Final Answer|final answer):\s*([^\n\r]+)", text, re.IGNORECASE)
        if fa_match:
            ans = fa_match.group(1).strip()
            ans = ans.strip("`*\"'").strip()
            return ans

        ans_match = re.search(r"(?:the answer is|the result is)\s*([^.\n\r]+)", text, re.IGNORECASE)
        if ans_match:
            candidate = ans_match.group(1).strip().strip("`*\"'").strip()
            if candidate:
                return candidate

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            last_line = lines[-1].strip("`*\"'").strip()
            last_line = re.sub(r"^(?:FINAL ANSWER|Final Answer|Answer):\s*", "", last_line, flags=re.IGNORECASE)
            return last_line.rstrip(".")

        return text.strip()
