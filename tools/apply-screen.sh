#!/usr/bin/env bash
# The screen /apply's browser draws on, on the VM (docs/apply.md): two user services,
# kept in tools/systemd/ and started with the VM (lingering is on).
#
#   tools/apply-screen.sh install     link and enable both services, and start them
#   tools/apply-screen.sh status
#   tools/apply-screen.sh restart
#   tools/apply-screen.sh uninstall   stop, disable and unlink them
#
# jobhunt-xvfb.service is a virtual screen (Xvfb :99); jobhunt-vnc.service serves
# it over VNC on the VM's loopback only (localhost:5901), with the password in
# ~/.config/jobhunt/vncpasswd (made once by Sean with `x11vnc -storepasswd`).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNITS=(jobhunt-xvfb.service jobhunt-vnc.service)
PASSWD="${XDG_CONFIG_HOME:-$HOME/.config}/jobhunt/vncpasswd"

install() {
  for tool in Xvfb x11vnc; do
    command -v "$tool" >/dev/null || { echo "✗ $tool isn't installed (docs/apply.md, setup step 1)." >&2; exit 1; }
  done
  if [ ! -s "$PASSWD" ]; then
    echo "✗ No VNC password yet. Make one (it prompts):" >&2
    echo "    mkdir -p ${PASSWD%/*} && x11vnc -storepasswd $PASSWD && chmod 600 $PASSWD" >&2
    exit 1
  fi
  if [ "$(stat -c %a "$PASSWD")" != "600" ]; then
    echo "✗ $PASSWD must be readable by you alone: chmod 600 $PASSWD" >&2
    exit 1
  fi
  for unit in "${UNITS[@]}"; do systemctl --user link "$HERE/systemd/$unit" >/dev/null; done
  systemctl --user daemon-reload
  systemctl --user enable --now "${UNITS[@]}" 2>/dev/null
  status
  echo "  From the Mac: ssh -N -L 5901:localhost:5901 sean@jupiter, then open vnc://localhost:5901"
}

status() {
  for unit in "${UNITS[@]}"; do printf '%-22s %s\n' "$unit" "$(systemctl --user is-active "$unit" || true)"; done
}

case "${1:-}" in
  install) install ;;
  status) status ;;
  restart) systemctl --user restart "${UNITS[@]}"; status ;;
  uninstall) systemctl --user disable --now "${UNITS[@]}" 2>/dev/null || true; status ;;
  *) echo "usage: $0 install|status|restart|uninstall" >&2; exit 2 ;;
esac
