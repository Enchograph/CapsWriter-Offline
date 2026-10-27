# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态
"""

from __future__ import annotations
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Timer
from typing import TYPE_CHECKING, Optional

from . import logger
from core.tools.my_status import Status
from core.client.ui.hint_window import HintWindow
 
if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.audio.recorder import AudioRecorder
    from core.client.app import CapsWriterClient



class ShortcutTask:
    """
    单个快捷键的录音任务

    跟踪每个快捷键独立的录音状态，防止互相干扰。
    """

    def __init__(self, app: CapsWriterClient, shortcut: Shortcut, recorder_class=None):
        """
        初始化快捷键任务

        Args:
            app: 客户端 App 实例
            shortcut: 快捷键配置
            recorder_class: AudioRecorder 类（可选，用于延迟导入）
        """
        self.app = app
        self.shortcut = shortcut
        self._recorder_class = recorder_class

        # 任务状态
        self.task: Optional[asyncio.Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False

        # 按键按下时刻（单调时钟，钩子线程写入），用于不受钩子阻塞影响的时长判定
        self.pressed_at: float = 0.0

        # 本次按下是否已被判定为短按
        self._cancelled = False

        # 串行执行器：开关音频流是百毫秒级阻塞操作，必须离开键盘钩子线程
        self._exec = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f'shortcut-{shortcut.key}',
        )
        self._launch_future = None
        # 长按阈值到达后显示提示窗的定时器
        self._hint_timer: Optional[Timer] = None

        # hold_mode 状态跟踪
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()

        # 线程池（用于 countdown）
        self.pool = None

        # 录音状态动画
        self._status = Status('开始录音', spinner='point')

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _get_recorder(self) -> AudioRecorder:
        """获取 AudioRecorder 实例"""
        if self._recorder_class is None:
            from core.client.audio.recorder import AudioRecorder
            self._recorder_class = AudioRecorder
        return self._recorder_class(self.app)

    def launch(self) -> None:
        """
        启动录音任务（键盘钩子线程调用，只做轻量状态变更）

        打开音频流耗时 250ms 以上，若在钩子线程里同步执行，会阻塞后续的
        按键事件处理，把短按的时长量成几百毫秒。阻塞部分交给执行器。
        """
        logger.info(f"[{self.shortcut.key}] 触发：开始录音")

        # 记录开始时间
        self.recording_start_time = time.time()
        self.pressed_at = time.perf_counter()
        self.is_recording = True
        self._cancelled = False

        self._launch_future = self._exec.submit(self._launch_stream)

    def _launch_stream(self) -> None:
        """打开音频流并启动识别协程（工作线程执行）"""
        if self._cancelled:
            return

        # 提示窗等按住超过长按阈值再显示，短按不在屏幕上闪现
        self._start_hint_timer()

        self.app.stream.open()

        # 打开期间被判为短按：本次不录音，收尾后退出
        if self._cancelled:
            self._close_stream()
            return

        # 确认要录音后才开始收数据并投递开始标志，避免短按在队列里留下孤儿消息
        self.state.start_recording(self.recording_start_time)
        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({'type': 'begin', 'time': self.recording_start_time, 'data': None}),
            self.app.loop
        )

        # 启动识别任务
        recorder = self._get_recorder()
        self.task = asyncio.run_coroutine_threadsafe(
            recorder.record_and_send(),
            self.app.loop,
        )

    def _start_hint_timer(self) -> None:
        """按住达到长按阈值后才显示提示窗"""
        timer = Timer(self.threshold, self._show_hint)
        timer.daemon = True
        self._hint_timer = timer
        timer.start()

    def _cancel_hint_timer(self) -> None:
        """取消尚未触发的提示窗定时器"""
        if self._hint_timer is not None:
            self._hint_timer.cancel()
            self._hint_timer = None

    def _show_hint(self) -> None:
        """阈值时刻显示提示窗：已收到音频则直接「录音中」，否则先「准备中」"""
        if not self.is_recording or self._cancelled:
            return

        if self.app.stream.receiving:
            HintWindow.show_recording()
        else:
            HintWindow.show_preparing()
        self._status.start()

    def cancel(self) -> None:
        """取消录音任务（时间过短，钩子线程调用）"""
        logger.debug(f"[{self.shortcut.key}] 取消录音任务（时间过短）")

        self.is_recording = False
        self._cancelled = True

        # 提示窗只是队列投递，直接在钩子线程收起，避免短按时闪现
        self._cancel_hint_timer()
        HintWindow.hide()

        # 还没开始打开音频流的话直接取消，不必白开一次设备
        if self._launch_future is not None:
            self._launch_future.cancel()
            self._launch_future = None

        self._exec.submit(self._cancel_stream)

    def _cancel_stream(self) -> None:
        """关闭音频流并清理本次录音（工作线程执行）"""
        self._close_stream()

        if self.task is not None:
            self.task.cancel()
            self.task = None

    def _close_stream(self) -> None:
        """停止录音状态与提示，再关闭音频流"""
        self.state.stop_recording()
        HintWindow.hide()
        self._status.stop()
        self.app.stream.close()

    def finish(self) -> None:
        """完成录音任务（钩子线程调用，只做轻量状态变更）"""
        logger.info(f"[{self.shortcut.key}] 释放：完成录音")

        self.is_recording = False
        self._cancel_hint_timer()

        self._exec.submit(self._finish_stream)

    def _finish_stream(self) -> None:
        """收尾本次录音并通知识别结束（工作线程执行）"""
        self.state.stop_recording()
        HintWindow.show_processing()
        self._status.stop()
        self.app.stream.close()

        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({
                'type': 'finish',
                'time': time.time(),
                'data': None
            }),
            self.app.loop
        )

        # 执行 restore（可恢复按键 + 非阻塞模式）
        # 阻塞模式下按键不会发送到系统，状态不会改变，不需要恢复
        if self.shortcut.is_toggle_key() and not self.shortcut.suppress:
            self._restore_key()

    def shutdown(self) -> None:
        """客户端退出时释放执行器"""
        if self.is_recording:
            self.cancel()
        self._exec.shutdown(wait=False)

    def _restore_key(self) -> None:
        """恢复按键状态（防自捕获逻辑由 ShortcutManager 处理）"""
        # 通知管理器执行 restore
        # 防自捕获：管理器会设置 flag 再发送按键
        manager = self._manager_ref()
        if manager:
            logger.debug(f"[{self.shortcut.key}] 自动恢复按键状态 (suppress={self.shortcut.suppress})")
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(f"[{self.shortcut.key}] manager 引用丢失，无法 restore")
