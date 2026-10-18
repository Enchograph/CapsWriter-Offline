# coding: utf-8
"""
录音状态悬浮提示窗

屏幕底部固定居中显示录音状态：
- 录音中：珊瑚红声波柱律动 + 「录音中」
- 识别中：暖黄三点依次点亮 + 「识别中…」

tkinter 界面运行在独立守护线程中，通过队列接收状态指令，线程安全。
"""

from __future__ import annotations

import ctypes
import math
import queue
import threading
import time
import tkinter as tk

from config_client import ClientConfig as Config

_W, _H = 160, 44
_BG = '#23262d'
_BORDER = '#3a3f4a'
_TEXT_COLOR = '#f5f5f7'
_WAVE_COLOR = '#ff6b6b'
_DOT_ON = '#ffd166'
_DOT_OFF = '#5a5240'
_TRANSPARENT = '#010203'

_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_TRANSPARENT = 0x00000020

_RECORDING_TEXT = '录音中'
_PROCESSING_TEXT = '识别中…'

_INDICATOR_XS = (20, 30, 40)


class _HintUIThread(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name='hint-ui')
        self.commands: queue.Queue[str] = queue.Queue()

    def run(self) -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self._state = ''
        self._visible = False
        self._shown_at = 0.0
        self._anim_at = 0.0
        self._proc_index = 0

        root = tk.Tk()
        self._root = root
        root.overrideredirect(True)
        root.attributes('-topmost', True)
        root.configure(bg=_TRANSPARENT)
        try:
            root.attributes('-transparentcolor', _TRANSPARENT)
        except tk.TclError:
            pass

        canvas = tk.Canvas(root, width=_W, height=_H, bg=_TRANSPARENT, highlightthickness=0)
        canvas.pack()
        self._canvas = canvas

        self._round_rect(1, 1, _W - 1, _H - 1, (_H - 2) / 2,
                         fill=_BG, outline=_BORDER, width=1)
        self._wave = [
            canvas.create_line(x, _H / 2, x, _H / 2, width=3,
                               fill=_WAVE_COLOR, capstyle='round')
            for x in _INDICATOR_XS
        ]
        self._proc_dots = [
            canvas.create_oval(x - 3, _H / 2 - 3, x + 3, _H / 2 + 3,
                               fill=_DOT_OFF, outline='')
            for x in _INDICATOR_XS
        ]
        self._text = canvas.create_text(52, _H / 2, text='', fill=_TEXT_COLOR, anchor='w',
                                        font=('Microsoft YaHei UI', 10))
        self._place_bottom_center()
        root.withdraw()

        # 不抢焦点、不在任务栏显示、鼠标点击穿透
        root.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE,
            style | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW | _WS_EX_TRANSPARENT)

        root.after(25, self._poll)
        root.mainloop()

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                  x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
                  x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self._canvas.create_polygon(points, smooth=True, **kwargs)

    def _poll(self) -> None:
        try:
            while True:
                self._apply(self.commands.get_nowait())
        except queue.Empty:
            pass

        now = time.monotonic()
        if self._visible and self._state == 'rec':
            if now - self._anim_at >= 0.06:
                self._anim_at = now
                t = now * 7.0
                for i, item in enumerate(self._wave):
                    half = 4 + 7 * abs(math.sin(t + i * 1.15))
                    x = _INDICATOR_XS[i]
                    self._canvas.coords(item, x, _H / 2 - half, x, _H / 2 + half)
        elif self._visible and self._state == 'proc':
            if now - self._anim_at >= 0.18:
                self._anim_at = now
                self._proc_index = (self._proc_index + 1) % 3
                for i, item in enumerate(self._proc_dots):
                    self._canvas.itemconfigure(item, fill=_DOT_ON if i == self._proc_index else _DOT_OFF)
            if now - self._shown_at > 10:
                self._apply('hide')
        self._root.after(25, self._poll)

    def _apply(self, command: str) -> None:
        if command == 'rec':
            self._set_state('rec', _RECORDING_TEXT)
        elif command == 'proc':
            self._set_state('proc', _PROCESSING_TEXT)
        elif command == 'hide':
            self._visible = False
            self._state = ''
            self._root.withdraw()

    def _set_state(self, state: str, text: str) -> None:
        self._state = state
        self._shown_at = time.monotonic()
        self._anim_at = 0.0
        self._proc_index = 0
        recording = state == 'rec'

        for i, item in enumerate(self._wave):
            self._canvas.itemconfigure(item, state='normal' if recording else 'hidden')
            if recording:
                x = _INDICATOR_XS[i]
                self._canvas.coords(item, x, _H / 2 - 7, x, _H / 2 + 7)
        for i, item in enumerate(self._proc_dots):
            self._canvas.itemconfigure(
                item,
                state='hidden' if recording else 'normal',
                fill=_DOT_ON if i == 0 else _DOT_OFF)

        self._canvas.itemconfigure(self._text, text=text)
        if not self._visible:
            self._visible = True
            self._root.deiconify()
        self._root.lift()

    def _place_bottom_center(self) -> None:
        class _Rect(ctypes.Structure):
            _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                        ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

        rect = _Rect()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        work_w = rect.right - rect.left
        work_h = rect.bottom - rect.top
        if work_w <= 0 or work_h <= 0:
            work_w = self._root.winfo_screenwidth()
            work_h = self._root.winfo_screenheight()
            rect.left, rect.top = 0, 0
        x = rect.left + (work_w - _W) // 2
        y = rect.top + work_h - _H - 72
        self._root.geometry(f'{_W}x{_H}+{x}+{y}')


class HintWindow:
    """线程安全的悬浮提示窗入口"""

    _thread: _HintUIThread | None = None
    _lock = threading.Lock()

    @classmethod
    def _ensure_started(cls) -> _HintUIThread | None:
        if not getattr(Config, 'hint_ui_enabled', True):
            return None
        with cls._lock:
            if cls._thread is None:
                cls._thread = _HintUIThread()
                cls._thread.start()
            return cls._thread

    @classmethod
    def show_recording(cls) -> None:
        t = cls._ensure_started()
        if t:
            t.commands.put('rec')

    @classmethod
    def show_processing(cls) -> None:
        t = cls._ensure_started()
        if t:
            t.commands.put('proc')

    @classmethod
    def hide(cls) -> None:
        t = cls._thread
        if t:
            t.commands.put('hide')
