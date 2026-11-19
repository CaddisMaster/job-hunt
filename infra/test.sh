#!/usr/bin/env bash
# Copied from Orbit Ops: a bare #N in these comments is an Orbit Ops issue.
# Test the app role (#322) in a throwaway LXD system container on the VM:
#   launch -> droplet-infra's host layer -> refusals without a proper .env and
#   certificate -> run -> run again (changed=0) -> verify -> check mode -> delete
#
#   infra/test.sh            # keeps nothing
#   KEEP=1 infra/test.sh     # leaves the container up to poke at
#
# It creates and deletes ONLY containers named apply-*. Containers, never --vm:
# the VM has no /dev/kvm. LXD must be initialised first (infra/README.md).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=infra/lib.sh
. "$here/lib.sh"
name="apply-app-$(date +%s)"

check_lxd

step "control node"
control_node

# --- nothing private is committable ------------------------------------------
step "the real inventory is gitignored"
git -C "$here/.." check-ignore -q infra/ansible/inventory.yml \
  || die "infra/ansible/inventory.yml is NOT gitignored; the droplet's address would be committable"
echo "ok: infra/ansible/inventory.yml is ignored"

# --- launch ------------------------------------------------------------------
step "launch $name"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"; [[ ${KEEP:-} == 1 ]] || delete_container "$name"' EXIT
launch_container "$name"

ct() { lxc exec "$name" -- "$@"; }

cat > "$tmp/inventory.yml" <<EOF
droplet:
  hosts:
    $name:
      ansible_connection: community.general.lxd
EOF
# A stand-in deploy key: production's is set in defaults/main.yml (RUNBOOK §4).
test_key='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIApplyTestDeployKeyxxxxxxxxxxxxxxxxxxxxxxxxxx github-actions job-hunt deploy'
play() { ansible-playbook -i "$tmp/inventory.yml" -e "job_hunt_deploy_key='$test_key'" "$@"; }
recap_ok() { grep -qE "^$name +: ok=[0-9]+ +changed=0 +unreachable=0 +failed=0" "$1"; }

# --- the host layer: droplet-infra's host.yml, at the pin ----------------------
step "droplet-infra's host layer"
play vendor/droplet-infra/infra/ansible/host.yml >"$tmp/host.log" || { cat "$tmp/host.log"; die "the host layer failed"; }
tail -3 "$tmp/host.log"

# A line no role here declares, as another tenant's deploy key is on production.
unmanaged='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIApplyTestUnmanagedKeyxxxxxxxxxxxxxxxxxxxxxxxxx another-tenant'
ct sh -c "echo '$unmanaged' >> /home/deploy/.ssh/authorized_keys"

# --- refusals: the key, .env, then the certificate ------------------------------------
step "no deploy key: the run must fail and say so"
# Production's key is in defaults/main.yml, so "no key" means overriding it empty.
if ansible-playbook -i "$tmp/inventory.yml" -e "job_hunt_deploy_key=''" app.yml >"$tmp/no-key.log" 2>&1; then die "the run passed with no deploy key"; fi
grep -q 'job_hunt_deploy_key is not set' "$tmp/no-key.log" || { cat "$tmp/no-key.log"; die "the failure doesn't name the key"; }
ct test ! -e /opt/apply || die "the role changed the host before refusing for a missing key"
echo "ok: refused without Job Hunt's own deploy key, changed nothing"

step "no .env: the run must fail and name the file"
if play app.yml >"$tmp/no-env.log" 2>&1; then die "the run passed with no .env"; fi
grep -q '/opt/apply/.env must exist' "$tmp/no-env.log" || { cat "$tmp/no-env.log"; die "the failure doesn't name .env"; }
ct test ! -e /opt/apply/.env || die "the role created .env"
echo "ok: refused, named /opt/apply/.env, wrote nothing"

step ".env mode 644: the run must fail and leave it alone"
ct sh -c 'printf "SECRET_KEY=test-only-not-a-secret\n" > /opt/apply/.env && chown deploy:deploy /opt/apply/.env && chmod 644 /opt/apply/.env'
before=$(ct sha256sum /opt/apply/.env)
if play app.yml >"$tmp/bad-mode.log" 2>&1; then die "the run passed with .env mode 644"; fi
grep -q '/opt/apply/.env must exist' "$tmp/bad-mode.log" || { cat "$tmp/bad-mode.log"; die "the failure doesn't name .env"; }
[[ $(ct stat -c %a /opt/apply/.env) == 644 ]] || die "the role changed .env's mode"
[[ $(ct sha256sum /opt/apply/.env) == "$before" ]] || die "the role changed .env's contents"
! grep -q 'test-only-not-a-secret' "$tmp/bad-mode.log" || die "the run printed .env's contents"
echo "ok: refused, named the file, left it unchanged"
ct chmod 600 /opt/apply/.env

step "no certificate: the run must fail and name it"
if play app.yml >"$tmp/no-cert.log" 2>&1; then die "the run passed with no certificate"; fi
grep -q 'live/apply.seandesmet.com/fullchain.pem is missing' "$tmp/no-cert.log" \
  || { cat "$tmp/no-cert.log"; die "the failure doesn't name the certificate"; }
echo "ok: refused, named the certificate"

stand_in_certificate "$name"

# --- #19: the first check, before anything of the app layer exists ------------
step "check mode on a host without the app layer (must not fail)"
play app.yml --check --diff | tee "$tmp/first-check.log" || true
grep -qE "^$name +: ok=[0-9]+ +changed=[1-9][0-9]* +unreachable=0 +failed=0" "$tmp/first-check.log" \
  || die "the first check mode failed, or reported nothing to change; see the recap above"
grep -q 'TASK \[job_hunt_app : The apply site is enabled\]' "$tmp/first-check.log" || die "the first check skipped the site link"
grep -q 'apply-backup.timer would be enabled and started' "$tmp/first-check.log" || die "the first check didn't report the timer"
ct test ! -e /etc/nginx/sites-enabled/apply.seandesmet.com || die "check mode created the site link"
ct test ! -e /etc/systemd/system/apply-backup.timer || die "check mode installed the timer"
echo "ok: the first check reports changes and fails nothing"

# --- first full run, then nothing to do ---------------------------------------
step "first run"
play app.yml

step "second run (must report changed=0)"
play app.yml | tee "$tmp/second.log"
recap_ok "$tmp/second.log" || die "the second run changed something or failed; see the recap above"

step "check mode (as on production; must report changed=0)"
play app.yml --check --diff | tee "$tmp/check.log"
recap_ok "$tmp/check.log" || die "check mode reports a change on a host the role just built"

# --- verify --------------------------------------------------------------------
step "verify"
[[ $(ct stat -c '%a %U:%G' /opt/apply) == '750 deploy:deploy' ]] || die "/opt/apply is not 750 deploy:deploy"
[[ $(ct stat -c '%a %U:%G' /opt/apply/backups) == '700 deploy:deploy' ]] || die "backups/ is not 700 deploy:deploy"
[[ $(ct stat -c '%a %U:%G' /opt/apply/.env) == '600 deploy:deploy' ]] || die ".env changed"
ct test ! -e /opt/apply/docker-compose.yml || die "the role wrote docker-compose.yml; release.yml owns it"

[[ $(ct readlink /etc/nginx/sites-enabled/apply.seandesmet.com) == /etc/nginx/sites-available/apply.seandesmet.com ]] \
  || die "the apply site is not enabled"
ct nginx -t

# The site proxies to 5004: stand a backend up there and go through Nginx.
ct sh -c 'mkdir -p /tmp/be && echo apply-backup-be > /tmp/be/index.html && cd /tmp/be &&
  nohup python3 -m http.server 5004 --bind 127.0.0.1 >/dev/null 2>&1 &'
sleep 1
body=$(ct curl -sk --resolve apply.seandesmet.com:443:127.0.0.1 https://apply.seandesmet.com/)
[[ $body == apply-backup-be ]] || die "https://apply.seandesmet.com/ did not reach 127.0.0.1:5004 (got: $body)"
redirect=$(ct curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --resolve apply.seandesmet.com:80:127.0.0.1 http://apply.seandesmet.com/x)
[[ $redirect == '301 https://apply.seandesmet.com/x' ]] || die "port 80 does not redirect to HTTPS (got: $redirect)"

keys=$(ct cat /home/deploy/.ssh/authorized_keys)
[[ $(grep -c 'github-actions job-hunt deploy$' <<<"$keys") == 1 ]] || die "Job Hunt's deploy key is not there exactly once"
grep -qx 'no-agent-forwarding,no-port-forwarding,no-X11-forwarding ssh-ed25519 \S* github-actions job-hunt deploy' <<<"$keys" \
  || die "the deploy key's line differs from production's shape"
grep -qxF "$unmanaged" <<<"$keys" || die "the unmanaged key line changed or vanished"

[[ $(ct systemctl is-enabled apply-backup.timer) == enabled ]] || die "the backup timer is not enabled"
[[ $(ct systemctl is-active apply-backup.timer) == active ]] || die "the backup timer is not active"
for u in apply-backup.service apply-backup.timer; do
  cmp -s <(ct cat "/etc/systemd/system/$u") "$here/systemd/$u" || die "$u differs from infra/systemd/$u"
done

ct test ! -e /etc/letsencrypt/accounts || die "an ACME account exists; something asked Let's Encrypt"

echo
echo "PASS: $name: refused without .env and a certificate, built, second run and check mode changed=0"
if [[ ${KEEP:-} == 1 ]]; then
  echo "KEEP=1: left $name running; delete it with: lxc delete --force $name"
fi
