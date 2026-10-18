# coding: utf-8
"""
录音状态悬浮提示窗

屏幕底部固定居中的音频可视化：
- 录音中：白色胶囊内单色对称波形，实时显示麦克风频谱
- 识别中：三颗灰色圆点依次点亮

tkinter 界面运行在独立守护线程中，音频回调通过 update_spectrum 推送频谱数据。
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
import tkinter as tk

from config_client import ClientConfig as Config

_W, _H = 220, 72
_BARS = 24
_BAR_WIDTH = 3
_BAR_SPACING = 8
_BAR_MAX_HALF = 26
_MID_Y = _H / 2
_TRANSPARENT = '#010203'
_BG = '#ffffff'
_BORDER = '#e5e5ea'
_BAR_COLOR = '#1c1c1e'
_DOT_ON = '#1c1c1e'
_DOT_OFF = '#d1d1d6'

_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_TRANSPARENT = 0x00000020

_BAR_XS = [_W / 2 + (i - (_BARS - 1) / 2) * _BAR_SPACING for i in range(_BARS)]


class _HintUIThread(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name='hint-ui')
        self.commands: queue.Queue[str] = queue.Queue()
        self.latest_levels = [-100.0] * _BARS

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
        self._levels = [0.0] * _BARS
        self._floors = [-80.0] * _BARS
        self._peaks = [-40.0] * _BARS

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
        self._bars = []
        for x in _BAR_XS:
            self._bars.append(canvas.create_line(
                x, _MID_Y, x, _MID_Y, width=_BAR_WIDTH,
                fill=_BAR_COLOR, capstyle='round'))
        dot_y = _H / 2
        self._proc_dots = [
            canvas.create_oval(x - 4, dot_y - 4, x + 4, dot_y + 4,
                               fill=_DOT_OFF, outline='')
            for x in (_W / 2 - 12, _W / 2, _W / 2 + 12)
        ]
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
            if now - self._anim_at >= 0.03:
                self._anim_at = now
                dbs = self.latest_levels
                for i, item in enumerate(self._bars):
                    db = dbs[i]
                    peak = self._peaks[i]
                    floor = self._floors[i]
                    # 逐频段 AGC：峰值快跟慢落，噪声底立即下探、缓慢恢复
                    peak = db if db > peak else max(floor, peak - 0.12)
                    floor = db if db < floor else min(peak, floor + 0.03)
                    self._peaks[i] = peak
                    self._floors[i] = floor
                    span = max(peak - floor, 12.0)
                    goal = (db - floor - 3.0) / span
                    goal = 0.0 if goal < 0.0 else (1.0 if goal > 1.0 else goal)
                    cur = self._levels[i]
                    # 起得快、落得慢，接近真实电平表的观感
                    cur += (goal - cur) * (0.55 if goal > cur else 0.18)
                    self._levels[i] = cur
                    half = max(1.5, cur * _BAR_MAX_HALF)
                    x = _BAR_XS[i]
                    self._canvas.coords(item, x, _MID_Y - half, x, _MID_Y + half)
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
            self._set_state('rec')
        elif command == 'proc':
            self._set_state('proc')
        elif command == 'hide':
            self._visible = False
            self._state = ''
            self._root.withdraw()

    def _set_state(self, state: str) -> None:
        self._state = state
        self._shown_at = time.monotonic()
        self._anim_at = 0.0
        self._proc_index = 0
        recording = state == 'rec'
        if recording:
            self._floors = [-80.0] * _BARS
            self._peaks = [-40.0] * _BARS
            self._levels = [0.0] * _BARS

        for item in self._bars:
            self._canvas.itemconfigure(item, state='normal' if recording else 'hidden')
        for i, item in enumerate(self._proc_dots):
            self._canvas.itemconfigure(
                item,
                state='hidden' if recording else 'normal',
                fill=_DOT_ON if i == 0 else _DOT_OFF)

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
    def update_spectrum(cls, levels: list[float]) -> None:
        t = cls._thread
        if t:
            t.latest_levels = levels

    @classmethod
    def hide(cls) -> None:
        t = cls._thread
        if t:
            t.commands.put('hide')
