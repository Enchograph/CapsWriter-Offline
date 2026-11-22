# coding: utf-8
"""
Wayland 录音状态悬浮提示窗（GTK4 + gtk4-layer-shell）

提示窗运行在独立子进程中，父进程通过 stdin 发送 JSON 行：
    {"cmd": "prep" | "rec" | "proc" | "hide"}
    {"levels": [24 个 dB 值]}
子进程也可直接运行：python -m core.client.ui.hint_window_gtk

视觉与 Windows 版一致：
- 准备中：灰色基线上的黑点正弦往返
- 录音中：白色胶囊内 24 根单色对称波形（逐频段 AGC）
- 转换中：三颗灰点依次点亮
"""

from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import sys
import threading
import time

from config_client import ClientConfig as Config

_W, _H = 220, 72
_BARS = 24
_BAR_WIDTH = 3
_BAR_SPACING = 8
_BAR_MAX_HALF = 26
_MID_Y = _H / 2
_BG = (1.0, 1.0, 1.0)
_BORDER = (0xe5 / 255, 0xe5 / 255, 0xea / 255)
_BAR_COLOR = (0x1c / 255, 0x1c / 255, 0x1e / 255)
_DOT_OFF = (0xd1 / 255, 0xd1 / 255, 0xd6 / 255)
_BASELINE_COLOR = (0xd1 / 255, 0xd1 / 255, 0xd6 / 255)
_BAR_XS = [_W / 2 + (i - (_BARS - 1) / 2) * _BAR_SPACING for i in range(_BARS)]
_LAYER_SHELL_PRELOAD = '/usr/lib/libgtk4-layer-shell.so'


class _HintUI:
    """子进程内的 GTK 提示窗"""

    def __init__(self) -> None:
        self.commands: queue.Queue = queue.Queue()
        self.latest_levels = [-100.0] * _BARS
        self._state = ''
        self._visible = False
        self._shown_at = 0.0
        self._anim_at = 0.0
        self._proc_index = 0
        self._levels = [0.0] * _BARS
        self._floors = [-80.0] * _BARS
        self._peaks = [-40.0] * _BARS
        self._window = None
        self._area = None

    # ---------- GTK 启动 ----------

    def run(self) -> None:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Gtk4LayerShell', '1.0')
        from gi.repository import GLib

        self._loop = GLib.MainLoop()
        threading.Thread(target=self._read_stdin, daemon=True, name='hint-stdin').start()
        self._on_activate(None)
        self._loop.run()

    def _read_stdin(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                self.commands.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        # 父进程退出（stdin EOF）时提示窗随之退出
        from gi.repository import GLib
        GLib.idle_add(self._loop.quit)

    def _on_activate(self, app) -> None:
        from gi.repository import Gtk, Gdk, GLib, Gtk4LayerShell as LS

        window = Gtk.Window()
        window.set_default_size(_W, _H)
        window.set_decorated(False)
        window.set_resizable(False)

        css = Gtk.CssProvider()
        css.load_from_string('window { background-color: transparent; }')
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        area = Gtk.DrawingArea()
        area.set_content_width(_W)
        area.set_content_height(_H)
        area.set_draw_func(self._draw)
        window.set_child(area)

        LS.init_for_window(window)
        LS.set_namespace(window, 'capswriter-hint')
        LS.set_layer(window, LS.Layer.OVERLAY)
        LS.set_keyboard_mode(window, LS.KeyboardMode.NONE)
        LS.set_anchor(window, LS.Edge.BOTTOM, True)
        LS.set_anchor(window, LS.Edge.LEFT, True)
        LS.set_margin(window, LS.Edge.BOTTOM, 72)

        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        monitor = monitors.get_item(0) if monitors.get_n_items() else None
        if monitor is not None:
            geo = monitor.get_geometry()
            LS.set_monitor(window, monitor)
            LS.set_margin(window, LS.Edge.LEFT, max(0, (geo.width - _W) // 2))

        self._window = window
        self._area = area
        window.set_visible(False)
        GLib.timeout_add(25, self._tick)

    # ---------- 命令与动画 ----------

    def _tick(self) -> bool:
        try:
            while True:
                self._apply(self.commands.get_nowait())
        except queue.Empty:
            pass

        now = time.monotonic()
        dirty = False
        if self._visible and self._state == 'prep' and now - self._shown_at > 3:
            self._apply({'cmd': 'hide'})
        elif self._visible and self._state == 'rec':
            if now - self._anim_at >= 0.03:
                self._anim_at = now
                dbs = self.latest_levels
                for i in range(_BARS):
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
                    cur += (goal - cur) * (0.55 if goal > cur else 0.18)
                    self._levels[i] = cur
                dirty = True
        elif self._visible and self._state == 'proc':
            if now - self._anim_at >= 0.18:
                self._anim_at = now
                self._proc_index = (self._proc_index + 1) % 3
                dirty = True
            if now - self._shown_at > 10:
                self._apply({'cmd': 'hide'})

        if dirty and self._area is not None:
            self._area.queue_draw()
        return True

    def _apply(self, msg: dict) -> None:
        if 'levels' in msg:
            self.latest_levels = msg['levels']
            return
        cmd = msg.get('cmd')
        if cmd in ('prep', 'rec', 'proc'):
            self._set_state(cmd)
        elif cmd == 'hide':
            self._visible = False
            self._state = ''
            if self._window is not None:
                self._window.set_visible(False)

    def _set_state(self, state: str) -> None:
        self._state = state
        self._shown_at = time.monotonic()
        self._anim_at = 0.0
        self._proc_index = 0
        if state == 'rec':
            self._floors = [-80.0] * _BARS
            self._peaks = [-40.0] * _BARS
            self._levels = [0.0] * _BARS
        self._visible = True
        if self._window is not None:
            self._window.set_visible(True)
            self._window.present()
        if self._area is not None:
            self._area.queue_draw()

    # ---------- 绘制 ----------

    def _draw(self, area, cr, width: int, height: int) -> None:
        if not self._visible:
            return

        r = (_H - 2) / 2
        x1, y1, x2, y2 = 1, 1, _W - 1, _H - 1
        cr.set_source_rgb(*_BG)
        cr.new_sub_path()
        cr.arc(x1 + r, y1 + r, r, math.pi, 1.5 * math.pi)
        cr.arc(x2 - r, y1 + r, r, 1.5 * math.pi, 2 * math.pi)
        cr.arc(x2 - r, y2 - r, r, 0, 0.5 * math.pi)
        cr.arc(x1 + r, y2 - r, r, 0.5 * math.pi, math.pi)
        cr.close_path()
        cr.fill_preserve()
        cr.set_line_width(1)
        cr.set_source_rgb(*_BORDER)
        cr.stroke()

        if self._state == 'prep':
            cr.set_line_width(2)
            cr.set_line_cap(1)  # round
            cr.set_source_rgb(*_BASELINE_COLOR)
            cr.move_to(26, _MID_Y)
            cr.line_to(_W - 26, _MID_Y)
            cr.stroke()

            half = (_W - 52) / 2 - 3
            x = _W / 2 + math.sin(time.monotonic() * 2.8) * half
            cr.set_source_rgb(*_BAR_COLOR)
            cr.arc(x, _MID_Y, 3, 0, 2 * math.pi)
            cr.fill()

        elif self._state == 'rec':
            cr.set_line_width(_BAR_WIDTH)
            cr.set_line_cap(1)  # round
            cr.set_source_rgb(*_BAR_COLOR)
            for i, x in enumerate(_BAR_XS):
                half = max(1.5, self._levels[i] * _BAR_MAX_HALF)
                cr.move_to(x, _MID_Y - half)
                cr.line_to(x, _MID_Y + half)
            cr.stroke()

        elif self._state == 'proc':
            for i, x in enumerate((_W / 2 - 12, _W / 2, _W / 2 + 12)):
                cr.set_source_rgb(*(_BAR_COLOR if i == self._proc_index else _DOT_OFF))
                cr.arc(x, _MID_Y, 4, 0, 2 * math.pi)
                cr.fill()


class _HintUIThread:
    """父进程侧的提示窗子进程管理器（接口对齐 Windows 版）"""

    def __init__(self) -> None:
        self.commands: queue.Queue = queue.Queue()
        self.latest_levels = [-100.0] * _BARS
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._spawn()

    def _spawn(self) -> None:
        with self._lock:
            if self._proc is not None:
                if self._proc.poll() is None:
                    return
                self._proc.wait()
            # gtk4-layer-shell 必须在 libwayland-client 之前加载，Arch 包提供了预加载库
            env = os.environ.copy()
            if os.path.exists(_LAYER_SHELL_PRELOAD):
                env['LD_PRELOAD'] = _LAYER_SHELL_PRELOAD
            self._proc = subprocess.Popen(
                [sys.executable, '-m', 'core.client.ui.hint_window_gtk'],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
            )

    def send(self, msg: dict) -> None:
        self._spawn()
        with self._lock:
            if self._proc is None or self._proc.stdin is None:
                return
            try:
                self._proc.stdin.write(json.dumps(msg) + '\n')
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self._proc = None

    def drain(self) -> None:
        try:
            while True:
                self.send(self.commands.get_nowait())
        except queue.Empty:
            pass


class HintWindow:
    """线程安全的悬浮提示窗入口"""

    _thread: _HintUIThread | None = None
    _lock = threading.Lock()

    #: 提示窗当前是否处于显示状态（音频回调据此判断要不要切成「录音中」）
    visible = False

    @classmethod
    def _ensure_started(cls) -> _HintUIThread | None:
        if not getattr(Config, 'hint_ui_enabled', True):
            return None
        with cls._lock:
            if cls._thread is None:
                cls._thread = _HintUIThread()
            return cls._thread

    @classmethod
    def preheat(cls) -> None:
        cls._ensure_started()

    @classmethod
    def show_preparing(cls) -> None:
        t = cls._ensure_started()
        if t:
            cls.visible = True
            t.send({'cmd': 'prep'})

    @classmethod
    def show_recording(cls) -> None:
        t = cls._ensure_started()
        if t:
            cls.visible = True
            t.send({'cmd': 'rec'})

    @classmethod
    def show_processing(cls) -> None:
        t = cls._ensure_started()
        if t:
            cls.visible = True
            t.send({'cmd': 'proc'})

    @classmethod
    def update_spectrum(cls, levels: list[float]) -> None:
        t = cls._thread
        if t:
            t.latest_levels = levels
            t.send({'levels': levels})

    @classmethod
    def hide(cls) -> None:
        cls.visible = False
        t = cls._thread
        if t:
            t.send({'cmd': 'hide'})


def _main() -> None:
    _HintUI().run()


if __name__ == '__main__':
    _main()
