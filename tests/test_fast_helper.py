"""The settings helper is started as a new process for every panel the owner opens, so its start-up time is felt directly.
Networking libraries load only when something really uses the network."""
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def loaded(statement: str) -> set[str]:
    out = subprocess.run([sys.executable, "-c", f"import sys\n{statement}\nprint(' '.join(m for m in ('httpx', 'http.server', 'asyncio') if m in sys.modules))"],
                         capture_output=True, text=True, env={"PYTHONPATH": SRC, "SYSTEMROOT": r"C:\Windows", "PATH": ""}, timeout=60)
    assert out.returncode == 0, out.stderr[-400:]
    return set(out.stdout.split())


def test_the_settings_helper_does_not_load_networking_to_answer_settings_calls():
    assert not loaded("import smara.native_management, smara.native_runtime, smara.native_search, smara.native_goals, smara.native_memory") & {"httpx", "http.server"}


def test_the_computer_and_teach_modules_stay_light_until_used():
    assert not loaded("import smara.native_computer, smara.native_teach") & {"httpx", "http.server", "asyncio"}


def test_the_search_tools_still_work_when_they_do_need_httpx():
    assert loaded("import smara.native_search as s\ns.httpx.Client") >= {"httpx"}


def test_the_shared_tool_helper_is_the_same_object_everywhere():
    from smara import mcp_tool, native_computer, native_tools
    assert native_tools.tool is mcp_tool.tool is native_computer.tool and native_tools.MAX_BYTES == mcp_tool.MAX_BYTES == 1_000_000
