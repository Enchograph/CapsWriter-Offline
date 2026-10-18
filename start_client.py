# coding: utf-8
import os
import sys

# 窗口化打包后标准流为 None，兜底到 devnull，避免裸 print 崩溃
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = sys.stdout
if sys.stdin is None:
    sys.stdin = open(os.devnull, 'r', encoding='utf-8')

from core.client import CapsWriterClient

if __name__ == "__main__":
    # 直接实例化并启动门面类即可
    # 环境初始化职责已下放至 CapsWriterClient
    CapsWriterClient().start()