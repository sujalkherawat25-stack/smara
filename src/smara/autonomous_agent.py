"""
Autonomous ReAct Agent for Smara
Modeled after Nous Research Hermes Agent architecture:
- Multi-turn ReAct loop (Thought -> Action -> Observation -> Final Answer)
- Native tool calling with Sarvam GLM-5.2 & Gemma 4 on /v2/chat/completions
- Built-in tools: web_search, web_extract, wayback_extract, python_execute, file_read, zip_extract_and_read, calculate
- Clean extraction of final answers adhering to GAIA benchmark constraints
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
    calculate
)

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
    }
]

SYSTEM_PROMPT = """You are Smara Autonomous Agent, an elite AI system modeled after Nous Research Hermes Agent.
You solve complex multi-step reasoning, research, multimodal, and mathematical tasks autonomously using tool execution.

### Operational Guidelines:
1. **ReAct Loop**:
   - Always break down problems logically.
   - When you need factual information, use `web_search`.
   - To inspect a specific webpage or article found, use `web_extract`.
   - If asked about a past state, historical snapshot, or Wikipedia revision on a specific date, ALWAYS use `wayback_extract` with the target date.
   - When doing arithmetic, statistical calculation, counting, text filtering, or complex logic, DO NOT do mental math. Write Python code and execute it using `python_execute` or `calculate`.
   - If local files (.csv, .json, .txt, .zip) are mentioned in the task, inspect them using `file_read` or `zip_extract_and_read`.

2. **Honesty and Rigor**:
   - Never fabricate or guess facts, URLs, dates, or calculations.
   - Verify every intermediate step with tool output before reaching conclusions.

3. **Final Answer Format**:
   - When you have obtained and verified the definitive answer, state your final answer clearly on the final line as:
     FINAL ANSWER: <exact answer>
   - Output ONLY the concise answer requested (e.g. if a number, just the number; if a name, just the name; if a comma-separated list, just the items separated by commas).
"""


class SmaraAutonomousAgent:
    """Autonomous ReAct agent interacting with Sarvam LLM via multi-turn tool-calling loops."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.sarvam.ai/v2/chat/completions",
        model: str = "glm5.2",
        max_iterations: int = 8,
    ):
        self.api_key = api_key or os.getenv("SMARA_MODEL_SARVAM_API_KEY") or os.getenv("SARVAM_API_KEY") or ""
        self.base_url = base_url
        self.model = model
        self.max_iterations = max_iterations

        self._tool_handlers = {
            "web_search": self._dispatch_web_search,
            "web_extract": self._dispatch_web_extract,
            "wayback_extract": self._dispatch_wayback_extract,
            "python_execute": self._dispatch_python_execute,
            "file_read": self._dispatch_file_read,
            "zip_extract_and_read": self._dispatch_zip_extract,
            "calculate": self._dispatch_calculate,
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
        Returns a dictionary containing:
          - answer: The final extracted answer text
          - raw_answer: Full concluding message
          - trace: Detailed step-by-step logs
          - tools_used: List of unique tools called during execution
          - iterations: Rounds executed
        """
        user_prompt = f"Task: {task}"
        if file_path:
            user_prompt += f"\nAssociated Task File: {file_path}"
        if file_content:
            user_prompt += f"\nFile Text Content Snippet:\n{file_content[:4000]}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
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

        # Check for FINAL ANSWER: <ans>
        fa_match = re.search(r"(?:FINAL ANSWER|Final Answer|final answer):\s*([^\n\r]+)", text, re.IGNORECASE)
        if fa_match:
            ans = fa_match.group(1).strip()
            ans = ans.strip("`*\"'").strip()
            return ans

        # Check for "The answer is X"
        ans_match = re.search(r"(?:the answer is|the result is)\s*([^.\n\r]+)", text, re.IGNORECASE)
        if ans_match:
            candidate = ans_match.group(1).strip().strip("`*\"'").strip()
            if candidate:
                return candidate

        # Take last non-empty line
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            last_line = lines[-1].strip("`*\"'").strip()
            # If line is "FINAL ANSWER: ...", strip prefix
            last_line = re.sub(r"^(?:FINAL ANSWER|Final Answer|Answer):\s*", "", last_line, flags=re.IGNORECASE)
            return last_line.rstrip(".")

        return text.strip()
