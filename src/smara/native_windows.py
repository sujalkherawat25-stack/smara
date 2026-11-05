"""Windows desktop backend: window listing, activation, UIA tree, SendInput.

Not an OS sandbox. Policy (grants, expiry, critical-action approval, secret-field
refusal) lives in native_computer.py; this module only provides mechanisms plus
a deny list of apps that are never automated (terminals, password managers,
security/auth UI, the Run dialog, other agent hosts).
"""
import ctypes
from ctypes import wintypes
import hashlib
import io
import os
from pathlib import Path
import re
import time

# Apps Smara must never drive, even with a desktop grant (mirrors Codex's
# non-negotiable list): terminals, Run, auth/security UI, password managers,
# agent hosts.
DENIED_EXES = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe", "openconsole.exe",
    "bash.exe", "wsl.exe", "mintty.exe", "powershell_ise.exe",
    "consent.exe", "credentialuibroker.exe", "logonui.exe", "lockapp.exe", "winlogon.exe", "securityhealthsystray.exe",
    "windowsdefender.exe", "msmpeng.exe", "msascui.exe", "mmc.exe", "regedit.exe", "secpol.msc", "taskmgr.exe",
    "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "lastpass.exe", "dashlane.exe", "nordpass.exe", "enpass.exe",
    "chatgpt.exe", "codex.exe", "claude.exe", "smara.exe", "smara-desktop.exe",
}
DENIED_TITLES = re.compile(r"^(run|windows security|user account control|windows defender|credential|sign in to your account)\b", re.I)
SENSITIVE_FIELD = re.compile(r"pass(word|code|phrase)|\botp\b|one[- ]?time|verification code|\bcvv\b|\bcvc\b|security code|card number|credit card|debit card|\bpin\b|\bssn\b|secret|api[ _-]?key|token", re.I)

VK = {"return": 0x0D, "enter": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
      "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "page_up": 0x21, "pageup": 0x21, "prior": 0x21, "page_down": 0x22,
      "pagedown": 0x22, "next": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
      "period": 0xBE, "comma": 0xBC, "minus": 0xBD, "equal": 0xBB, "plus": 0xBB, "slash": 0xBF, "backslash": 0xDC,
      "semicolon": 0xBA, "apostrophe": 0xDE, "grave": 0xC0, "bracketleft": 0xDB, "bracketright": 0xDD,
      "kp_add": 0x6B, "numpad_add": 0x6B, "kp_subtract": 0x6D, "numpad_subtract": 0x6D, "kp_multiply": 0x6A, "numpad_multiply": 0x6A,
      "kp_divide": 0x6F, "numpad_divide": 0x6F, "kp_decimal": 0x6E, "numpad_decimal": 0x6E, "kp_enter": 0x0D, "numpad_enter": 0x0D}
MODIFIERS = {"control": 0x11, "ctrl": 0x11, "control_l": 0x11, "control_r": 0x11, "shift": 0x10, "shift_l": 0x10, "shift_r": 0x10,
             "alt": 0x12, "alt_l": 0x12, "alt_r": 0x12, "menu": 0x12}
FORBIDDEN_KEYS = {"meta", "windows", "win", "super", "cmd", "command", "os", "meta_l", "meta_r", "super_l", "super_r", "win_l", "win_r"}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}


def parse_chord(chord):
    """'Control_L+Shift+a' -> ([modifier vks], key vk). Rejects the Windows key."""
    if not isinstance(chord, str) or not chord.strip() or len(chord) > 80: raise ValueError("Invalid key chord")
    parts = [p.strip() for p in chord.split("+") if p.strip()] if chord.strip() != "+" else ["plus"]
    if chord.strip().endswith("++"): parts = [p.strip() for p in chord.strip()[:-2].split("+") if p.strip()] + ["plus"]
    mods, key = [], None
    for part in parts:
        low = part.lower()
        if low in FORBIDDEN_KEYS or low.startswith(("win+", "windows+", "meta+")): raise ValueError("The Windows key is never allowed")
        if low in MODIFIERS: mods.append(MODIFIERS[low]); continue
        if key is not None: raise ValueError("Only one non-modifier key per chord")
        if low in VK: key = VK[low]
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", low): key = 0x70 + int(low[1:]) - 1
        elif re.fullmatch(r"(kp|numpad)_[0-9]", low): key = 0x60 + int(low[-1])
        elif len(part) == 1 and part.isalnum() and part.isascii(): key = ord(part.upper())
        else: raise ValueError("Unknown key: " + part[:20])
    if key is None: raise ValueError("Chord has no key")
    return mods, key


LAUNCH = {"notepad": ["notepad.exe"], "calc": ["calc.exe"], "paint": ["mspaint.exe"],
          "excel": ["C:/Program Files/Microsoft Office/root/Office16/EXCEL.EXE", "C:/Program Files (x86)/Microsoft Office/root/Office16/EXCEL.EXE"],
          "tally": ["C:/Program Files/TallyPrime/tally.exe", "C:/Program Files (x86)/TallyPrime/tally.exe", "C:/Program Files/Tally.ERP9/tally.exe", "C:/Program Files (x86)/Tally.ERP9/tally.exe"]}


def resolve_launch(name):
    """Only a fixed set of installed apps can be started; never an arbitrary path."""
    for candidate in LAUNCH.get(name, []):
        if os.path.isabs(candidate):
            if os.path.exists(candidate): return candidate
        else: return candidate
    raise ValueError("App is not installed or not launchable: " + str(name))


class ForegroundRequired(ValueError):
    """Window could not be brought forward; no input was sent."""


ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


MOVE, LDOWN, LUP, RDOWN, RUP, MDOWN, MUP, WHEEL, HWHEEL, VIRTUALDESK, ABSOLUTE = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x800, 0x1000, 0x4000, 0x8000
KEYUP, UNICODE, EXTENDEDKEY, SCANCODE = 0x2, 0x4, 0x1, 0x8


class WindowsBackend:
    def __init__(self):
        if os.name != "nt": raise RuntimeError("Windows interactive desktop required")
        import psutil
        import win32gui
        import win32process
        self.psutil, self.gui, self.proc = psutil, win32gui, win32process
        self.user32 = ctypes.windll.user32
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user32.SendInput.restype = wintypes.UINT
        try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            try: self.user32.SetProcessDPIAware()
            except (OSError, AttributeError): pass
        self._cache = {}  # observation id -> {index: UIAElementInfo}

    # ---- windows -------------------------------------------------------
    def _info(self, hwnd):
        pid = self.proc.GetWindowThreadProcessId(hwnd)[1]
        process = self.psutil.Process(pid)
        exe = process.exe()
        return {"id": str(hwnd), "handle": hwnd, "pid": pid, "created": process.create_time(), "exe": exe,
                "app": Path(exe).name.lower(), "title": self.gui.GetWindowText(hwnd)[:240]}

    def _denied(self, info):
        return info["app"] in DENIED_EXES or bool(DENIED_TITLES.match(info["title"])) and info["app"] in {"explorer.exe", "securityhealthui.exe", "consent.exe"}

    def _cloaked(self, hwnd):
        value = ctypes.c_int(0)
        try: ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), 4)
        except OSError: return False
        return bool(value.value)

    def targets(self):
        found = []
        def visit(hwnd, _):
            try:
                if not self.gui.IsWindowVisible(hwnd) or self._cloaked(hwnd) or not self.gui.GetWindowText(hwnd): return
                if self.gui.GetWindowLong(hwnd, -20) & 0x80: return  # tool windows
                info = self._info(hwnd)
                if self._denied(info) or info["pid"] == os.getpid(): return
                left, top, right, bottom = self.gui.GetWindowRect(hwnd)
                info.update(rect=[left, top, right, bottom], minimized=bool(self.gui.IsIconic(hwnd)),
                            foreground=hwnd == self.gui.GetForegroundWindow())
                found.append(info)
            except (OSError, self.psutil.Error, Exception):
                return
        self.gui.EnumWindows(visit, None)
        return found[:60]

    def window(self, target):
        """Re-validate a previously returned window; ownership must not change."""
        hwnd = int(target["handle"])
        if not self.gui.IsWindow(hwnd): raise ValueError("Selected window closed")
        info = self._info(hwnd)
        if self._denied(info): raise ValueError("That app is never automated")
        for key in ("pid", "created", "exe"):
            if info[key] != target[key]: raise ValueError("Window/process ownership changed")
        return hwnd

    def foreground_ok(self, hwnd):
        fg = self.gui.GetForegroundWindow()
        return fg == hwnd or (fg and self.proc.GetWindowThreadProcessId(fg)[1] == self.proc.GetWindowThreadProcessId(hwnd)[1])

    def activate(self, target):
        hwnd = self.window(target)
        if self.gui.IsIconic(hwnd): self.gui.ShowWindow(hwnd, 9)
        if not self.foreground_ok(hwnd):
            fg = self.gui.GetForegroundWindow()
            mine = ctypes.windll.kernel32.GetCurrentThreadId()
            theirs = self.proc.GetWindowThreadProcessId(fg)[0] if fg else 0
            attached = bool(theirs and theirs != mine and self.user32.AttachThreadInput(mine, theirs, True))
            try:
                self.user32.BringWindowToTop(hwnd); self.user32.SetForegroundWindow(hwnd)
            finally:
                if attached: self.user32.AttachThreadInput(mine, theirs, False)
            deadline = time.monotonic() + 1.5
            while not self.foreground_ok(hwnd) and time.monotonic() < deadline: time.sleep(.05)
        if not self.foreground_ok(hwnd): raise ForegroundRequired("Windows refused to bring the window forward; no input sent")
        return hwnd

    def launch(self, path):
        os.startfile(path)  # noqa: S606 - path vetted by policy layer

    # ---- observation ---------------------------------------------------
    def _uia(self):
        from pywinauto.uia_element_info import UIAElementInfo
        return UIAElementInfo

    def _focused(self, hwnd):
        try:
            from pywinauto.uia_element_info import UIAElementInfo, IUIA
            info = UIAElementInfo(IUIA().iuia.GetFocusedElement())
            if info.element.CurrentProcessId != self.proc.GetWindowThreadProcessId(hwnd)[1]: return None
            return self._describe(info, None, with_value=False)
        except Exception:
            return None

    def _describe(self, info, origin, with_value=True):
        r = info.rectangle
        element = info.element
        try: password = bool(element.CurrentIsPassword)
        except Exception: password = False
        name = (info.name or "")[:200]
        auto_id = (info.automation_id or "")[:100]
        item = {"type": info.control_type, "name": name, "automation_id": auto_id, "enabled": bool(info.enabled), "password": password,
                "sensitive": password or bool(SENSITIVE_FIELD.search(name + " " + auto_id)),
                "rect": [r.left, r.top, r.right, r.bottom]}
        if origin: item["rel"] = [r.left - origin[0], r.top - origin[1], r.right - origin[0], r.bottom - origin[1]]
        if with_value and info.control_type in {"Edit", "Document", "ComboBox", "DataItem", "Text"} and not item["sensitive"]:
            try:
                from pywinauto.controls.uiawrapper import UIAWrapper
                wrapper = UIAWrapper(info)
                value = wrapper.iface_value.CurrentValue if info.control_type != "Document" else wrapper.iface_text.DocumentRange.GetText(2000)
                item["value"] = (value or "")[:500]
            except Exception: pass
        return item

    def observe(self, target, observation_id, screenshot=True, text=True, max_elements=400):
        hwnd = self.activate(target)
        UIAElementInfo = self._uia()
        left, top, right, bottom = self.gui.GetWindowRect(hwnd)
        elements, handles, visited = [], {}, 0
        root = UIAElementInfo(hwnd)
        if text:
            queue = [(root, 0)]
            while queue and len(elements) < max_elements and visited < 2500:
                node, depth = queue.pop(0)
                try: children = node.children()
                except Exception: continue
                for child in children:
                    visited += 1
                    try:
                        if child.element.CurrentIsOffscreen: continue
                        item = self._describe(child, (left, top))
                    except Exception: continue
                    r = item["rect"]
                    if r[2] <= r[0] or r[3] <= r[1]: continue
                    item["depth"] = depth
                    interesting = item["type"] not in {"Pane", "Group", "Custom", "Image", "Separator"} or item["name"]
                    if interesting and len(elements) < max_elements:
                        item["index"] = len(elements); handles[item["index"]] = child; elements.append(item)
                    queue.append((child, depth + 1))
        image, capture = (self._capture(hwnd, (left, top, right, bottom)) if screenshot else (None, None))
        shown = self.foreground_ok(hwnd)
        fingerprint = hashlib.sha256(repr([(e["type"], e["name"], e.get("value", "")) for e in elements]).encode()).hexdigest()
        self._cache[observation_id] = handles
        for old in list(self._cache)[:-3]: self._cache.pop(old, None)
        return {"elements": elements, "focused": self._focused(hwnd), "image": image, "capture": capture, "fingerprint": fingerprint,
                "rect": [left, top, right, bottom], "title": self.gui.GetWindowText(hwnd), "foreground": bool(shown),
                "truncated": bool(len(elements) >= max_elements or visited >= 2500)}

    def _capture(self, hwnd, rect):
        from PIL import Image, ImageGrab
        import win32ui
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width < 1 or height < 1 or width * height > 16_000_000: raise ValueError("Window capture bounds unsupported")
        dc = self.gui.GetWindowDC(hwnd)
        source = win32ui.CreateDCFromHandle(dc); memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap(); bitmap.CreateCompatibleBitmap(source, width, height)
        previous = memory.SelectObject(bitmap)
        image, mode = None, "window"
        try:
            if self.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2):
                image = Image.frombuffer("RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1).copy()
        finally:
            memory.SelectObject(previous)
            self.gui.DeleteObject(bitmap.GetHandle()); memory.DeleteDC(); source.DeleteDC(); self.gui.ReleaseDC(hwnd, dc)
        if image is None or image.getbbox() is None:
            # GPU-composited windows render black through PrintWindow. The window is
            # foreground at this point, so a screen crop is what the user sees,
            # but it may include overlays from other apps; flagged to the model.
            image, mode = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True), "screen-crop"
        stream = io.BytesIO(); image.save(stream, format="PNG")
        if len(stream.getvalue()) > 10 * 1024 * 1024: raise ValueError("Screenshot exceeds 10 MB")
        return stream.getvalue(), mode

    # ---- input ----------------------------------------------------------
    def _send(self, *inputs):
        array = (INPUT * len(inputs))(*inputs)
        if self.user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)) != len(inputs): raise OSError("SendInput was blocked")

    def _mouse(self, flags, x=0, y=0, data=0):
        return INPUT(0, _INPUTUNION(mi=MOUSEINPUT(x, y, data & 0xFFFFFFFF, flags, 0, 0)))

    def _key(self, vk=0, flags=0, scan=0):
        return INPUT(1, _INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))

    def _norm(self, x, y):
        vx, vy = self.user32.GetSystemMetrics(76), self.user32.GetSystemMetrics(77)
        vw, vh = max(self.user32.GetSystemMetrics(78), 2), max(self.user32.GetSystemMetrics(79), 2)
        return int((x - vx) * 65535 / (vw - 1)), int((y - vy) * 65535 / (vh - 1))

    def _move_to(self, x, y, steps=14):
        """Real pointer travel: interpolated absolute moves with ease-out."""
        point = wintypes.POINT(); self.user32.GetCursorPos(ctypes.byref(point))
        for step in range(1, steps + 1):
            t = step / steps; t = 1 - (1 - t) ** 2
            nx, ny = self._norm(point.x + (x - point.x) * t, point.y + (y - point.y) * t)
            self._send(self._mouse(MOVE | ABSOLUTE | VIRTUALDESK, nx, ny)); time.sleep(.008)

    def _assert_target_point(self, hwnd, x, y):
        under = self.user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not under or self.proc.GetWindowThreadProcessId(under)[1] != self.proc.GetWindowThreadProcessId(hwnd)[1]:
            raise ForegroundRequired("The point is over a non-target window; re-activate and re-observe")

    def _absolute(self, target, x, y):
        hwnd = self.activate(target)
        left, top, right, bottom = self.gui.GetWindowRect(hwnd)
        if not (0 <= x <= right - left and 0 <= y <= bottom - top): raise ValueError("Coordinate is outside the window")
        self._assert_target_point(hwnd, left + x, top + y)
        return hwnd, left + x, top + y

    def click(self, target, x, y, button="left", count=1):
        hwnd, sx, sy = self._absolute(target, x, y)
        self._move_to(sx, sy)
        down, up = {"left": (LDOWN, LUP), "right": (RDOWN, RUP), "middle": (MDOWN, MUP)}[button]
        for _ in range(count):
            self._send(self._mouse(down)); time.sleep(.03); self._send(self._mouse(up)); time.sleep(.05)

    def click_element(self, target, observation_id, index, expected_rect, **kwargs):
        element = self._element(observation_id, index)
        r = element.rectangle
        if abs(r.left - expected_rect[0]) > 4 or abs(r.top - expected_rect[1]) > 4: raise ValueError("Element moved since the observation; re-observe")
        left, top = self.gui.GetWindowRect(self.window(target))[:2]
        self.click(target, (r.left + r.right) // 2 - left, (r.top + r.bottom) // 2 - top, **kwargs)

    def rect(self, target):
        return list(self.gui.GetWindowRect(self.window(target)))

    def _element(self, observation_id, index, writing=False):
        element = self._cache.get(observation_id, {}).get(index)
        if element is None: raise ValueError("Unknown element index for that observation")
        try:
            if element.element.CurrentIsOffscreen: raise ValueError("Element is no longer visible; re-observe")
            if writing and bool(element.element.CurrentIsPassword): raise ValueError("Password fields are never written")
        except ValueError: raise
        except Exception: raise ValueError("Element is gone; re-observe") from None
        return element

    def type_text(self, target, text, permitted=lambda: True, check_focus=None):
        self.activate(target)
        for chunk_start in range(0, len(text), 40):
            if not permitted(): raise ValueError("Computer control revoked or expired")
            if check_focus: check_focus()
            if not self.foreground_ok(self.window(target)): raise ForegroundRequired("Focus left the target window; typing stopped")
            for ch in text[chunk_start:chunk_start + 40]: self._type_char(ch)
            time.sleep(.02)

    def _type_char(self, ch):
        if ch == "\n": return self._chord([], 0x0D)
        if ch == "\t": return self._chord([], 0x09)
        mapped = self.user32.VkKeyScanW(ord(ch)) if ord(ch) < 0x10000 else -1
        vk, shift = (mapped & 0xFF, (mapped >> 8) & 1) if mapped != -1 and not (mapped >> 8) & 6 else (None, 0)
        if vk is None or vk == 0xFF:
            code = ord(ch)
            units = [code] if code < 0x10000 else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
            for unit in units: self._send(self._key(0, UNICODE, unit), self._key(0, UNICODE | KEYUP, unit))
            return
        self._chord([0x10] if shift else [], vk)

    def _chord(self, mods, vk):
        down = []
        try:
            for m in mods: self._send(self._key(m, 0, self.user32.MapVirtualKeyW(m, 0))); down.append(m)
            flags = EXTENDEDKEY if vk in EXTENDED else 0
            scan = self.user32.MapVirtualKeyW(vk, 0)
            self._send(self._key(vk, flags, scan)); time.sleep(.012); self._send(self._key(vk, flags | KEYUP, scan))
        finally:
            for m in reversed(down): self._send(self._key(m, KEYUP, self.user32.MapVirtualKeyW(m, 0)))

    def press_key(self, target, chord):
        mods, vk = parse_chord(chord)
        self.activate(target)
        self._chord(mods, vk)

    def scroll(self, target, x, y, dx, dy):
        hwnd, sx, sy = self._absolute(target, x, y)
        self._move_to(sx, sy, steps=8)
        if dy: self._send(self._mouse(WHEEL, data=-int(round(dy / 120)) * 120 or (-120 if dy > 0 else 120)))
        if dx: self._send(self._mouse(HWHEEL, data=int(round(dx / 120)) * 120 or (120 if dx > 0 else -120)))

    def drag(self, target, x1, y1, x2, y2):
        hwnd, sx, sy = self._absolute(target, x1, y1)
        left, top = self.gui.GetWindowRect(hwnd)[:2]
        if not (0 <= x2 <= self.gui.GetWindowRect(hwnd)[2] - left and 0 <= y2 <= self.gui.GetWindowRect(hwnd)[3] - top): raise ValueError("Drag end is outside the window")
        self._move_to(sx, sy)
        self._send(self._mouse(LDOWN)); time.sleep(.08)
        try: self._move_to(left + x2, top + y2, steps=24)
        finally: time.sleep(.05); self._send(self._mouse(LUP))

    def set_value(self, target, observation_id, index, value):
        self.activate(target)
        element = self._element(observation_id, index, writing=True)
        from pywinauto.controls.uiawrapper import UIAWrapper
        wrapper = UIAWrapper(element)
        if element.control_type not in {"Edit", "Document", "ComboBox", "DataItem", "Text"}: raise ValueError("Element is not editable")
        if wrapper.iface_value.CurrentIsReadOnly: raise ValueError("Element is read-only")
        wrapper.iface_value.SetValue(value)

    def focused(self, target):
        return self._focused(self.window(target))
