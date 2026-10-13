"""Small Windows UIA backend, not an OS sandbox or unrestricted desktop agent."""
import hashlib
import io
import json
import os
from pathlib import Path
import time

APPS = {"notepad.exe", "calculatorapp.exe", "calc.exe", "mspaint.exe"}


class ForegroundRequired(ValueError):
    """Only focus is missing; ownership failures must never be retried."""


class WindowsBackend:
    def __init__(self):
        if os.name != "nt": raise RuntimeError("Windows interactive desktop required")
        import psutil
        import win32gui
        from pywinauto import Desktop
        self.psutil, self.gui, self.desktop = psutil, win32gui, Desktop(backend="uia")

    def _target(self, window):
        process = self.psutil.Process(window.process_id())
        executable = Path(process.exe()).resolve(strict=True)
        # No user-built EXE merely renamed to an allowed app. This is still an
        # experimental allowlist, not signature verification or an OS boundary.
        roots = [Path(os.environ.get("SystemRoot", r"C:\Windows")), Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WindowsApps"]
        if executable.name.lower() not in APPS or not any(executable.is_relative_to(root.resolve()) for root in roots):
            raise ValueError("App is not in the initial trusted desktop allowlist")
        return {"id": str(window.handle), "handle": window.handle, "pid": process.pid,
                "created": process.create_time(), "exe": str(executable), "title": window.window_text()[:240]}

    def targets(self):
        result = []
        for window in self.desktop.windows(visible_only=True):
            try: result.append(self._target(window))
            except (ValueError, OSError, self.psutil.Error): continue
        return result[:30]

    def _window(self, target):
        if not self.gui.IsWindow(target["handle"]): raise ValueError("Selected window closed")
        window = self.desktop.window(handle=target["handle"]).wrapper_object()
        current = self._target(window)
        for key in ("pid", "created", "exe", "handle"):
            if current[key] != target[key]: raise ValueError("Window/process ownership changed")
        if self.gui.GetForegroundWindow() != target["handle"] or window.is_minimized():
            raise ForegroundRequired("Bring the selected app to the foreground yourself; modal/background control is blocked")
        return window

    def wait_foreground(self, target, permitted):
        # Clicking a native approval puts Smara in front. Give the human a
        # bounded handoff, without activating any app or retrying an action.
        deadline = time.monotonic() + 10
        while True:
            if not permitted(): raise ValueError("Computer control revoked or expired")
            try:
                self._window(target)
                return
            except ForegroundRequired:
                if time.monotonic() >= deadline:
                    raise ForegroundRequired("Foreground handoff timed out; no app action was dispatched") from None
                time.sleep(.1)

    def _elements(self, window):
        wrappers = window.descendants()
        if len(wrappers) > 2000: raise ValueError("App accessibility tree exceeds bounded observation")
        entries = []
        for control in wrappers:
            info = control.element_info
            if control.process_id() != window.process_id() or not control.is_visible(): continue
            if bool(info.element.CurrentIsPassword): continue
            kind = info.control_type
            if kind not in {"Button", "Edit", "Text", "Document"}: continue
            label = control.window_text()[:1000]
            if any(word in label.casefold() for word in ("password", "permission", "sign in", "log in", "security", "delete", "install", "approve", "settings")): continue
            rich_edit = kind == "Document" and bool(control.handle) and self.gui.GetClassName(control.handle).casefold() in {"richeditd2dpt", "richedit50w"}
            actions = (["invoke"] if kind == "Button" else ["set_value"] if kind == "Edit" or rich_edit else []) if control.is_enabled() else []
            rect = control.rectangle()
            content = ""
            if kind == "Document": content = control.iface_text.DocumentRange.GetText(100001)
            elif kind == "Edit": content = control.iface_value.CurrentValue
            if len(content) > 100000: raise ValueError("Document exceeds complete-state validation bound; use project file tools")
            entries.append({"ref": "e" + str(len(entries)), "runtime_id": list(info.runtime_id), "type": kind,
                            "text": label, "value_preview": content[:1000], "value_truncated": len(content) > 1000,
                            "value_sha256": hashlib.sha256(content.encode()).hexdigest(),
                            "actions": actions, "rect": [rect.left, rect.top, rect.right, rect.bottom]})
            if len(entries) >= 200: break
        return entries

    def observe(self, target):
        window = self._window(target)
        elements = self._elements(window)
        image = self._capture(window)
        self._window(target)  # Focus/ownership must survive capture.
        stream = io.BytesIO(); image.save(stream, format="PNG")
        if len(stream.getvalue()) > 10 * 1024 * 1024: raise ValueError("Screenshot exceeds 10 MB")
        fingerprint = hashlib.sha256(json.dumps({"title": window.window_text(), "elements": elements}, sort_keys=True).encode()).hexdigest()
        return {"elements": elements, "fingerprint": fingerprint, "image": stream.getvalue(),
                "element_limit": 200, "elements_may_be_incomplete": len(elements) == 200}

    def _capture(self, window):
        # Never crop the global desktop: overlays can belong to other apps.
        # PrintWindow asks the selected HWND to render itself; unsupported/black
        # captures fail closed, without ImageGrab or screen-coordinate fallback.
        import ctypes
        from ctypes import wintypes
        import win32ui
        from PIL import Image
        rect = window.rectangle(); width, height = rect.width(), rect.height()
        if width < 1 or height < 1 or width * height > 16_000_000: raise ValueError("Window capture bounds unsupported")
        dc = self.gui.GetWindowDC(window.handle)
        source = win32ui.CreateDCFromHandle(dc); memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap(); bitmap.CreateCompatibleBitmap(source, width, height)
        previous_bitmap = memory.SelectObject(bitmap)
        try:
            print_window = ctypes.windll.user32.PrintWindow
            print_window.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
            print_window.restype = wintypes.BOOL
            if not print_window(window.handle, memory.GetSafeHdc(), 2): raise ValueError("Selected window capture unsupported")
            image = Image.frombuffer("RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1).copy()
            if image.getbbox() is None: raise ValueError("Selected window returned a black capture")
            return image
        finally:
            memory.SelectObject(previous_bitmap)
            self.gui.DeleteObject(bitmap.GetHandle()); memory.DeleteDC(); source.DeleteDC(); self.gui.ReleaseDC(window.handle, dc)

    def act(self, target, runtime_id, action, value):
        window = self._window(target)
        entry = next((e for e in self._elements(window) if e["runtime_id"] == runtime_id), None)
        if not entry or action not in entry["actions"]: raise ValueError("Element changed or unsupported")
        control = next((c for c in window.descendants() if list(c.element_info.runtime_id) == runtime_id), None)
        if not control or bool(control.element_info.element.CurrentIsPassword): raise ValueError("Element unavailable")
        self._window(target)
        if action == "invoke": control.invoke()
        elif action == "set_value":
            if control.element_info.control_type == "Edit": control.set_edit_text(value)
            else:
                import win32con
                if not control.handle or not self.gui.IsChild(window.handle, control.handle) or self.gui.GetClassName(control.handle).casefold() not in {"richeditd2dpt", "richedit50w"}:
                    raise ValueError("Document does not support bounded control-local replacement")
                self.gui.SendMessageTimeout(control.handle, win32con.WM_SETTEXT, 0, value, win32con.SMTO_ABORTIFHUNG, 2000)
        else: raise ValueError("Unsupported action")
        # No coordinate/key fallback or automatic retry after UIA errors.
