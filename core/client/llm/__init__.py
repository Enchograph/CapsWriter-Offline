"""
LLM 模块

提供 LLM 相关的所有功能，包括角色管理、消息构建、上下文管理等
LLM 关闭时只导出与平台无关的记录写入，避免拉起键盘监听等依赖。
"""

from core import get_logger
logger = get_logger('client')

from config_client import ClientConfig as Config

if not Config.llm_enabled:
    from .llm_write_md import write_llm_md

    __all__ = ['write_llm_md']
else:
    # 核心处理器
    from .llm_handler import LLMHandler, LLMResult

    # 角色配置和加载
    from .llm_role_config import RoleConfig
    from .llm_role_loader import RoleLoader

    # 上下文管理
    from .llm_context import ContextManager

    # 消息构建和客户端池
    from .llm_message_builder import MessageBuilder
    from .llm_client_pool import ClientPool

    # 剪贴板/选中文字
    from .llm_clipboard import copy_to_clipboard
    from .llm_get_selection import (
        get_selected_text,
        record_selection_usage
    )

    # 监控和输出
    from .llm_watcher import LLMFileWatcher
    from .llm_stop_monitor import StopMonitor

    from .llm_output_toast import handle_toast_mode
    from .llm_output_typing import handle_typing_mode


    __all__ = [
        # 核心
        'LLMHandler',

        # 角色管理
        'RoleConfig',
        'RoleLoader',

        # 上下文
        'ContextManager',

        # 组件
        'MessageBuilder',
        'ClientPool',

        # 剪贴板/选中文字
        'get_selected_text',
        'record_selection_usage',
        'copy_to_clipboard',

        # 监控
        'LLMFileWatcher',
        'StopMonitor',

        # 输出
        'handle_toast_mode',
        'handle_typing_mode',

        # 主入口
        'LLMResult',
    ]
