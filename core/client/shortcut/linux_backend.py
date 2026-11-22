# coding: utf-8
"""
Linux/Wayland 快捷键后端

按键由 evsieve 在 evdev 层捕获并吞掉 CapsLock，随后通过信号通知本进程：
    SIGUSR1 = 触发键按下
    SIGUSR2 = 触发键松开

evsieve 侧的辅助脚本 capswriter-key 负责读取 pid 文件并发送信号。
短按补发 CapsLock 使用 wtype（zwp_virtual_keyboard_v1）。
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Callable

from . import logger

_PIDFILE = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / 'capswriter' / 'client.pid'


def _wtype(*args: str) -> None:
    try:
        subprocess.run(
            ['wtype', *args],
            timeout=2,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception as e:
        logger.warning(f"wtype 调用失败: {e}")


class LinuxKeyEmulator:
    """Windows 版 ShortcutEmulator 的 Linux 等价物（接口保持一致）"""

    def is_emulating(self, key_name: str) -> bool:
        return False

    def clear_emulating_flag(self, key_name: str) -> None:
        pass

    def emulate_key(self, key_name: str) -> None:
        if key_name == 'caps_lock':
            _wtype('-k', 'Caps_Lock')

    def emulate_mouse_click(self, button_name: str) -> None:
        pass


class LinuxShortcutListener:
    """监听 evsieve 的按键信号，把按下/松开分发给事件处理器"""

    def __init__(self, on_down: Callable[[], None], on_up: Callable[[], None]):
        self._on_down = on_down
        self._on_up = on_up
        self._installed = False

    def start(self) -> None:
        _PIDFILE.parent.mkdir(parents=True, exist_ok=True)
        _PIDFILE.write_text(str(os.getpid()))

        def _down(signum, frame):
            try:
                self._on_down()
            except Exception as e:
                logger.warning(f"处理按下信号失败: {e}")

        def _up(signum, frame):
            try:
                self._on_up()
            except Exception as e:
                logger.warning(f"处理松开信号失败: {e}")

        signal.signal(signal.SIGUSR1, _down)
        signal.signal(signal.SIGUSR2, _up)
        self._installed = True
        logger.info(f"按键监听已就绪（evsieve 信号，pid 文件 {_PIDFILE}）")

    def stop(self) -> None:
        if self._installed:
            signal.signal(signal.SIGUSR1, signal.SIG_DFL)
            signal.signal(signal.SIGUSR2, signal.SIG_DFL)
            self._installed = False
        try:
            _PIDFILE.unlink()
        except FileNotFoundError:
            pass
