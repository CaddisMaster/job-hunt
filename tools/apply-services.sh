#!/usr/bin/env bash
# Applying's user services on the VM (docs/apply.md), kept in tools/systemd/ and
# started with the VM (lingering is on):
#
#   jobhunt-xvfb.service    the virtual screen (Xvfb :99) the browser draws on
#   jobhunt-vnc.service     VNC for it on the VM's loopback only (localhost:5901), with
#                           the password in ~/.config/jobhunt/vncpasswd (Sean's)
#   jobhunt-runner.service  takes the runs queued in the web app (#88)
#
#   tools/apply-services.sh install [URL]   link/generate, enable and start all three;
#                                           URL is Job Hunt's (default production)
#   tools/apply-services.sh status
#   tools/apply-services.sh restart
#   tools/apply-services.sh uninstall
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNITS=(jobhunt-xvfb.service jobhunt-vnc.service jobhunt-runner.service)
REPO="$(cd "$HERE/.." && pwd)"
USER_UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
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
  local url="${1:-https://apply.seandesmet.com}"
  for unit in jobhunt-xvfb.service jobhunt-vnc.service; do systemctl --user link "$HERE/systemd/$unit" >/dev/null; done
  mkdir -p "$USER_UNITS"
  sed -e "s#@REPO@#$REPO#g" -e "s#@URL@#$url#g" "$HERE/systemd/jobhunt-runner.service.in" >"$USER_UNITS/jobhunt-runner.service"
  systemctl --user daemon-reload
  echo "  The runner talks to $url"
  systemctl --user enable --now "${UNITS[@]}" 2>/dev/null
  status
  echo "  From the Mac: ssh -N -L 5901:localhost:5901 sean@jupiter, then open vnc://localhost:5901"
}

status() {
  for unit in "${UNITS[@]}"; do printf '%-22s %s\n' "$unit" "$(systemctl --user is-active "$unit" || true)"; done
}

case "${1:-}" in
  install) install "${2:-}" ;;
  status) status ;;
  restart) systemctl --user restart "${UNITS[@]}"; status ;;
  logs) journalctl --user -u jobhunt-runner.service -n "${2:-50}" --no-pager ;;
  uninstall)
    systemctl --user disable --now "${UNITS[@]}" 2>/dev/null || true
    rm -f "$USER_UNITS/jobhunt-runner.service"
    systemctl --user daemon-reload
    status ;;
  *) echo "usage: $0 install [URL]|status|restart|logs [N]|uninstall" >&2; exit 2 ;;
esac
