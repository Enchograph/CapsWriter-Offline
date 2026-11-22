#!/bin/sh
# 抓取内置键盘：CapsLock 交给客户端做语音触发，其余按键原样转发
exec evsieve \
    --input /dev/input/by-path/platform-i8042-serio-0-event-kbd grab \
    --hook key:capslock:1 exec-shell="${HOME}/localrepo/CapsWriter-Offline/scripts/capswriter-key down" \
    --hook key:capslock:0 exec-shell="${HOME}/localrepo/CapsWriter-Offline/scripts/capswriter-key up" \
    --block key:capslock \
    --output
