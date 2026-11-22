#!/bin/sh
# systemd 启动时 niri-session 可能还没把 WAYLAND_DISPLAY 导入 user manager，
# 主动从运行时目录探测 socket，保证 wtype / wl-copy / 提示窗可用。
if [ -z "$WAYLAND_DISPLAY" ]; then
    for sock in "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"/wayland-*; do
        case "$sock" in
            *.lock) continue ;;
        esac
        [ -S "$sock" ] || continue
        WAYLAND_DISPLAY=$(basename "$sock")
        export WAYLAND_DISPLAY
        break
    done
fi

exec "${HOME}/localrepo/CapsWriter-Offline/.venv/bin/python" start_client.py
