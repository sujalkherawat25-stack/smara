"""Turns the Markdown an answer is written in into WhatsApp's own formatting, Telegram's HTML, and chat-sized parts.

WhatsApp does not render Markdown: **bold**, # headings, --- rules and | tables arrive as raw symbols. WhatsApp
has its own marks, which this module uses:
  *bold*   _italic_   ~strike~   ```monospace```   and plain '•' bullets.
Telegram gets the same text with those marks turned into HTML tags (<b>, <i>, <s>, <pre>), every other <, > and &
escaped, so a stray symbol can never make Telegram refuse the message.

A long answer is split into a few parts at paragraph breaks (never inside a word, and a code block that is cut is closed
and reopened), so each chat bubble is short enough to read and under each channel's limit (WhatsApp text 4096
characters, a WhatsApp button card 1024, Telegram 4096). Nothing here raises: unknown shapes pass through unchanged."""
import html
import re

PART_CHARS = 1500        # one chat bubble: a long answer is split near here, at a paragraph break
CARD_CHARS = 1000        # the text of a message with buttons (WhatsApp allows 1024)
MAX_PARTS = 4            # more than this is a file, not a chat: the rest is cut with TOO_LONG
MAX_CHARS = 20_000       # a runaway answer is cut here before any other work
TOO_LONG = "… (Baaki hissa bahut lamba hai. Poora chahiye to 'PDF bhejo' ya 'Excel bhejo' likhiye.)"

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s*([-*_=])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_BULLET = re.compile(r"^(\s*)[*+-]\s+")
_QUOTE = re.compile(r"^\s*>\s?")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_IMAGE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
_BOLD3 = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BOLD_U = re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_ESCAPED = re.compile(r"\\([*_#|`~>\-.!\[\]()])")
_HTML_TAG = {re.compile(r"</?(?:b|strong)>", re.I): "*", re.compile(r"</?(?:i|em)>", re.I): "_",
             re.compile(r"</?(?:s|del|strike)>", re.I): "~"}
_BR = re.compile(r"<br\s*/?>", re.I)
_URL = re.compile(r"https?://[^\s<>()]+")


def _table_line(line: str) -> str:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    cells = [c.replace("**", "") for c in cells if c]
    return "• " + " · ".join(cells) if cells else ""


def _inline(line: str) -> str:
    line = _ESCAPED.sub(r"\1", line)
    line = _BR.sub("\n", line)
    for tag, mark in _HTML_TAG.items():
        line = tag.sub(mark, line)
    line = _IMAGE.sub(lambda m: m.group(1), line)
    line = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", line)
    line = _BOLD3.sub(lambda m: f"*{m.group(1)}*", line)
    line = _BOLD.sub(lambda m: f"*{m.group(1)}*", line)
    line = _BOLD_U.sub(lambda m: f"*{m.group(1)}*", line)
    line = _STRIKE.sub(lambda m: f"~{m.group(1)}~", line)
    line = _CODE.sub(lambda m: f"```{m.group(1)}```", line)
    return _lone_star(line)


def _lone_star(line: str) -> str:
    """An odd '*' (a bold mark with no partner) shows as a raw symbol on WhatsApp: drop the one stuck to a word.
    A '*' with spaces on both sides (5 * 3) is left alone, and so is anything in a URL or inline code."""
    if "```" in line or "*" not in line:
        return line
    marks = [m.start() for m in re.finditer(r"\*", line)
             if not (_space_at(line, m.start() - 1) and _space_at(line, m.start() + 1))]
    if len(marks) % 2 == 0:
        return line
    i = marks[-1]
    return line[:i] + line[i + 1:]


def _space_at(s: str, i: int) -> bool:
    return i < 0 or i >= len(s) or s[i].isspace()


def _pipe_row(line: str) -> bool:
    """'Name | Amount | Due' without the outer bars is still a table row."""
    return line.count(" | ") >= 2 and "```" not in line


def to_whatsapp(text: str) -> str:
    """WhatsApp-ready text. Never raises: unknown shapes pass through unchanged. Safe to run twice."""
    if not isinstance(text, str) or not text:
        return "" if text is None else str(text)
    out: list[str] = []
    in_code = False
    for raw in text[:MAX_CHARS].replace("\r\n", "\n").replace("\r", "\n").split("\n"):
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
        if _TABLE_ROW.match(line) or _pipe_row(line):
            line = _inline(_table_line(line))
        else:
            if (h := _HEADING.match(line)):
                line = f"*{h.group(1).replace('**', '').replace('*', '').strip()}*"
            line = _QUOTE.sub("", line)
            line = _BULLET.sub(lambda m: f"{m.group(1)}• ", line)
            line = _inline(line)
        out.append(line.rstrip())
    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    if result.count("```") % 2:                      # an unclosed fence would swallow the rest of the message
        result += "\n```"
    return result


_PRE = re.compile(r"```(.*?)```", re.DOTALL)
_T_BOLD = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_T_ITALIC = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_T_STRIKE = re.compile(r"(?<![\w~])~(?=\S)([^~\n]+?)(?<=\S)~(?![\w~])")


def wa_to_html(wa_text: str) -> str:
    """Telegram HTML for text that is already in WhatsApp's marks. Escapes everything first; links and code are kept
    out of the bold/italic rules, so an underscore in a URL never becomes italics."""
    esc = html.escape(wa_text or "", quote=False)
    keep: list[str] = []

    def hold(s: str) -> str:
        keep.append(s)
        return f"\x00{len(keep) - 1}\x00"
    esc = _PRE.sub(lambda m: hold(f"<pre>{m.group(1).strip(chr(10))}</pre>"), esc)
    esc = _URL.sub(lambda m: hold(m.group(0)), esc)
    esc = _T_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", esc)
    esc = _T_ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", esc)
    esc = _T_STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", esc)
    return re.sub("\x00(\\d+)\x00", lambda m: keep[int(m.group(1))], esc)


def to_telegram_html(text: str) -> str:
    """Telegram text in HTML mode from Markdown or WhatsApp-marked text."""
    return wa_to_html(to_whatsapp(text))


def html_to_plain(text: str) -> str:
    """The text of a Telegram HTML message without its tags: the fallback when Telegram refuses the HTML."""
    return html.unescape(re.sub(r"</?(?:b|i|s|u|pre|code|a)(?:\s[^>]*)?>", "", text or ""))


# ---- splitting a long answer into chat-sized parts ----------------------------------------------------------------------

def _blocks(text: str) -> list[str]:
    """Paragraphs (separated by a blank line), with a code block kept whole as one paragraph."""
    blocks, cur, in_code = [], [], False
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_code = not in_code
        if not line.strip() and not in_code:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def _hard_split(line: str, limit: int) -> list[str]:
    out = []
    while len(line) > limit:
        cut = line.rfind(" ", 0, limit)
        cut = cut if cut > limit // 2 else limit
        out.append(line[:cut].rstrip())
        line = line[cut:].lstrip()
    return out + ([line] if line else [])


def _pieces(block: str, limit: int) -> list[tuple[str, str]]:
    """(text, joiner): a block that fits is one piece; a longer one is cut at line breaks, then at spaces."""
    if len(block) <= limit:
        return [(block, "\n\n")]
    out = []
    for line in block.split("\n"):
        for i, bit in enumerate(_hard_split(line, limit)):
            out.append((bit, "\n" if out else "\n\n") if i == 0 else (bit, " "))
    return out


def _balance_fences(parts: list[str]) -> list[str]:
    fixed, carry = [], False
    for p in parts:
        if carry:
            p = "```\n" + p
        carry = p.count("```") % 2 == 1
        fixed.append(p + "\n```" if carry else p)
    return fixed


def split_text(text: str, limit: int = PART_CHARS, max_parts: int = MAX_PARTS) -> list[str]:
    """`text` as at most `max_parts` parts of at most `limit` characters, cut at paragraph breaks where possible. Too
    much for that many parts ends with TOO_LONG. Empty text gives no parts."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    room = limit - 8                                   # space for a code fence closed and reopened at a cut
    parts: list[str] = []
    cur = ""
    for block in _blocks(text):
        for piece, joiner in _pieces(block, room):
            if not cur:
                cur = piece
            elif len(cur) + len(joiner) + len(piece) <= room:
                cur += joiner + piece
            else:
                parts.append(cur)
                cur = piece
    if cur:
        parts.append(cur)
    if len(parts) > max_parts:
        last = parts[max_parts - 1]
        parts = parts[:max_parts - 1] + [last[:room - len(TOO_LONG) - 2].rstrip() + "\n\n" + TOO_LONG]
    return _balance_fences(parts)
