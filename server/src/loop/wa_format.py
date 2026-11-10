"""Turns the Markdown an answer is written in into WhatsApp's own formatting.

WhatsApp does not render Markdown: **bold**, # headings, --- rules and | tables arrive as raw symbols. WhatsApp
has its own marks, which this module uses:
  *bold*   _italic_   ~strike~   ```monospace```   and plain '•' bullets.
Only text is changed. Code blocks keep their content. Anything else is left as it was."""
import re

MAX_CHARS = 4000

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_BULLET = re.compile(r"^(\s*)[*+-]\s+")
_QUOTE = re.compile(r"^\s*>\s?")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_IMAGE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BOLD_U = re.compile(r"__(?=\S)(.+?)(?<=\S)__")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_CODE = re.compile(r"`([^`\n]+)`")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _table_line(line: str) -> str:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    cells = [c for c in cells if c]
    return "• " + " · ".join(cells) if cells else ""


def _inline(line: str) -> str:
    line = _IMAGE.sub(lambda m: m.group(1), line)
    line = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", line)
    line = _BOLD.sub(lambda m: f"*{m.group(1)}*", line)
    line = _BOLD_U.sub(lambda m: f"*{m.group(1)}*", line)
    line = _STRIKE.sub(lambda m: f"~{m.group(1)}~", line)
    line = _CODE.sub(lambda m: f"```{m.group(1)}```", line)
    return line


def to_whatsapp(text: str) -> str:
    """WhatsApp-ready text. Never raises: unknown shapes pass through unchanged."""
    if not isinstance(text, str) or not text:
        return "" if text is None else str(text)
    out: list[str] = []
    in_code = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(raw):
            in_code = not in_code
            out.append("```")
            continue
        if in_code:
            out.append(raw)
            continue
        line = raw
        if _RULE.match(line):
            out.append("")
            continue
        if _TABLE_SEP.match(line) and "-" in line:
            continue
        if _TABLE_ROW.match(line):
            line = _table_line(line)
        else:
            if (h := _HEADING.match(line)):
                line = f"*{h.group(1).strip()}*"
            line = _QUOTE.sub("", line)
            line = _BULLET.sub(lambda m: f"{m.group(1)}• ", line)
            line = _inline(line)
        out.append(line.rstrip())
    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    if result.count("```") % 2:                      # an unclosed fence would swallow the rest of the message
        result += "\n```"
    return result[:MAX_CHARS]


_PRE = re.compile(r"```(.*?)```", re.DOTALL)
_T_BOLD = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_T_ITALIC = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_T_STRIKE = re.compile(r"(?<![\w~])~(?=\S)([^~\n]+?)(?<=\S)~(?![\w~])")


def to_telegram_html(text: str) -> str:
    """Telegram text in HTML mode: the WhatsApp marks become <b>, <i>, <s> and <pre>. Everything is escaped first,
    so a stray < or & in an answer cannot break the message."""
    src = to_whatsapp(text)
    esc = src.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    esc = _PRE.sub(lambda m: f"<pre>{m.group(1).strip(chr(10))}</pre>", esc)
    esc = _T_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", esc)
    esc = _T_ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", esc)
    esc = _T_STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", esc)
    return esc[:MAX_CHARS]
