# coding: utf-8
"""
录音状态悬浮提示窗（平台分派）

Windows 使用 tkinter + Win32 扩展样式，Linux/Wayland 使用 GTK4 + layer-shell 子进程。
两套实现暴露相同的 HintWindow 接口。
"""

import sys

if sys.platform == 'win32':
    from .hint_window_win import HintWindow, _HintUIThread
else:
    from .hint_window_gtk import HintWindow, _HintUIThread

__all__ = ['HintWindow']
