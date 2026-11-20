#!/usr/bin/env bash
# The screen /apply's browser draws on, on the VM (docs/apply.md).
#
#   tools/apply-screen.sh start    a virtual screen (Xvfb :99) and VNC for it
#   tools/apply-screen.sh stop
#   tools/apply-screen.sh status
#
# Claude Code's Playwright browser opens on display :99. Sean watches and uses it
# from his Mac's Screen Sharing, through an SSH tunnel: VNC listens on the VM's
# loopback only (-localhost), never the network, and needs the password stored in
# ~/.config/jobhunt/vncpasswd (made once with `x11vnc -storepasswd`, by Sean).
set -euo pipefail

DISPLAY_NUM="${JOBHUNT_DISPLAY:-99}"
PORT="${JOBHUNT_VNC_PORT:-5901}"   # not 5900: the Mac's own Screen Sharing may hold that end of the tunnel
SIZE="${JOBHUNT_SCREEN:-1440x900x24}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/jobhunt"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/jobhunt"
PASSWD="$CONFIG/vncpasswd"

running() { [ -f "$STATE/$1.pid" ] && kill -0 "$(cat "$STATE/$1.pid")" 2>/dev/null; }

start() {
  for tool in Xvfb x11vnc; do
    command -v "$tool" >/dev/null || { echo "✗ $tool isn't installed (docs/apply.md, setup step 1)." >&2; exit 1; }
  done
  if [ ! -s "$PASSWD" ]; then
    echo "✗ No VNC password yet. Make one (it prompts):" >&2
    echo "    mkdir -p $CONFIG && x11vnc -storepasswd $PASSWD && chmod 600 $PASSWD" >&2
    exit 1
  fi
  if [ "$(stat -c %a "$PASSWD")" != "600" ]; then
    echo "✗ $PASSWD must be readable by you alone: chmod 600 $PASSWD" >&2
    exit 1
  fi
  mkdir -p "$STATE"
  if ! running xvfb; then
    Xvfb ":$DISPLAY_NUM" -screen 0 "$SIZE" -nolisten tcp >"$STATE/xvfb.log" 2>&1 &
    echo $! >"$STATE/xvfb.pid"
    sleep 1
    running xvfb || { echo "✗ Xvfb didn't start; see $STATE/xvfb.log" >&2; exit 1; }
  fi
  if ! running x11vnc; then
    x11vnc -display ":$DISPLAY_NUM" -localhost -rfbport "$PORT" -rfbauth "$PASSWD" \
      -forever -shared -noxdamage -quiet -o "$STATE/x11vnc.log" >/dev/null 2>&1 &
    echo $! >"$STATE/x11vnc.pid"
    sleep 1
    running x11vnc || { echo "✗ x11vnc didn't start; see $STATE/x11vnc.log" >&2; exit 1; }
  fi
  echo "✓ Screen :$DISPLAY_NUM is up; VNC on the VM's localhost:$PORT."
  echo "  On the Mac:  ssh -N -L $PORT:localhost:$PORT <the VM>   (leave it running)"
  echo "  then:        open vnc://localhost:$PORT"
}

stop() {
  for name in x11vnc xvfb; do
    if running "$name"; then kill "$(cat "$STATE/$name.pid")"; fi
    rm -f "$STATE/$name.pid"
  done
  echo "✓ Stopped."
}

status() {
  for name in xvfb x11vnc; do
    if running "$name"; then echo "$name: running"; else echo "$name: stopped"; fi
  done
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
