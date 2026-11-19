"""#5 — Job Hunt's layer of the droplet as code: what can be checked without a
container. Ported from Orbit Ops's tests/test_infra.py (its #322 and #384).

The role itself is tested in LXD by `infra/test.sh` (a fresh build, the
refusals, changed=0 on a second run and in check mode). These hold the rules a
one-line diff could break: exact pins, a host role pinned by SHA, an inventory
that can't be committed, and a role that never writes what release.yml owns.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra"
ROLE = INFRA / "ansible/roles/job_hunt_app"

# The pins, in the form droplet-infra's tests/test_infra_pins.py accepts.
_EXACT = re.compile(r"^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*$")
_ANY_ANSIBLE_PIN = re.compile(r"^\s*ansible(?:-core)?\s*(?:==|~=|>=|<=|!=|<|>)", re.M)


def _lines(path):
    return [s for line in path.read_text().splitlines() if (s := line.strip()) and not s.startswith("#")]


def _role_tasks():
    tasks = []
    for path in sorted((ROLE / "tasks").glob("*.yml")) + [ROLE / "handlers/main.yml"]:
        tasks += yaml.safe_load(path.read_text()) or []
    return tasks


def test_ansible_core_is_pinned_exactly():
    lines = _lines(INFRA / "requirements-infra.txt")
    assert any(line.startswith("ansible-core==") for line in lines)
    assert [line for line in lines if not _EXACT.match(line)] == []


def test_ansible_core_is_pinned_in_one_file_only():
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".txt", ".toml", ".cfg", ".yml", ".yaml", ".in"}:
            continue
        # vendor/ is droplet-infra's checkout, with its own (identical) pin.
        if {".git", ".venv", "venv", "collections", "vendor", "node_modules"} & set(path.parts):
            continue
        if _ANY_ANSIBLE_PIN.search(path.read_text(errors="ignore")):
            hits.append(path.relative_to(ROOT).as_posix())
    assert hits == ["infra/requirements-infra.txt"]


def test_every_collection_is_pinned_exactly():
    collections = yaml.safe_load((INFRA / "ansible/requirements.yml").read_text())["collections"]
    assert collections
    loose = {c["name"]: c.get("version") for c in collections if not re.fullmatch(r"\d+\.\d+\.\d+", str(c.get("version")))}
    assert loose == {}


def test_the_host_role_is_pinned_by_full_sha():
    pin = dict(line.split("=", 1) for line in _lines(INFRA / "host-role.pin"))
    assert pin["repo"] == "https://github.com/CaddisMaster/droplet-infra.git"
    assert re.fullmatch(r"[0-9a-f]{40}", pin["sha"])


def test_the_real_inventory_and_fetched_code_are_gitignored():
    # The container has no git; infra/test.sh asks `git check-ignore` the same
    # question on the VM before every run.
    ignore = _lines(ROOT / ".gitignore")
    for path in ("infra/ansible/inventory.yml", "infra/ansible/collections/", "infra/ansible/vendor/"):
        assert path in ignore
    assert not [line for line in ignore if line.startswith("!infra")]
    # And the example that is committed holds no real address.
    example = (INFRA / "ansible/inventory.example.yml").read_text()
    assert re.findall(r"\b\d+\.\d+\.\d+\.\d+\b", example) == ["203.0.113.10"]  # RFC 5737


def test_infra_stays_out_of_the_image():
    assert "infra/" in _lines(ROOT / ".dockerignore")


def test_the_role_never_writes_env_or_compose():
    """release.yml owns both; the role only checks .env."""
    writers = {"ansible.builtin.copy", "ansible.builtin.template", "ansible.builtin.lineinfile",
               "ansible.builtin.blockinfile", "ansible.builtin.file", "ansible.builtin.get_url"}
    for task in _role_tasks():
        for module in writers & task.keys():
            dest = str(task[module].get("dest") or task[module].get("path"))
            assert ".env" not in dest and "docker-compose" not in dest, task["name"]


def test_the_role_installs_the_backup_units_from_infra_systemd():
    """One copy of each unit: the role reads infra/systemd/, never a copy of its own."""
    assert not (ROLE / "files/apply-backup.service").exists()
    copies = [t for t in _role_tasks() if t["name"] == "The backup units"]
    assert copies[0]["ansible.builtin.copy"]["src"] == "{{ playbook_dir }}/../systemd/{{ item }}"


@pytest.mark.criterion(5, "The app is served over HTTPS at its address")
def test_the_site_proxies_apply_to_the_published_port_with_a_certificate():
    site = (ROLE / "files/apply.seandesmet.com").read_text()
    port = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["web"]["ports"][0].split(":")[1]
    assert port == "5004"
    assert site.count(f"proxy_pass http://127.0.0.1:{port};") == 1
    assert site.count("server_name apply.seandesmet.com;") == 2
    assert "ssl_certificate /etc/letsencrypt/live/apply.seandesmet.com/fullchain.pem;" in site
    assert "return 301 https://$host$request_uri;" in site
    # No security headers here: the app sets them (RUNBOOK §2).
    assert "add_header" not in site


def _deploy_commands(path):
    """Every `docker compose up` a deploy script runs, comments left out."""
    text = (ROOT / path).read_text()
    lines = [line.strip() for line in text.splitlines() if not line.lstrip().startswith("#")]
    return [line for line in lines if "docker compose up" in line]


def _release_deploy_text():
    steps = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]["deploy"]["steps"]
    return "\n".join(step.get("run", "") for step in steps)


def _db_start_script():
    """release.yml's two lines that start db, as the droplet's shell receives them."""
    lines = [line.strip() for line in _release_deploy_text().splitlines()]
    start = next(i for i, line in enumerate(lines) if line.startswith('DB_ID='))
    script = "\n".join(lines[start : start + 2]).removesuffix('"')
    return script.replace('\\"', '"').replace("\\$", "$")


def _run_db_start(tmp_path, ps_output, ps_exit=0):
    calls = tmp_path / "calls"
    stub = tmp_path / "docker"
    stub.write_text(
        '#!/bin/sh\n'
        f'echo "$*" >> "{calls}"\n'
        'case "$*" in\n'
        f'  "compose ps -aq db") printf "%s" "{ps_output}"; exit {ps_exit} ;;\n'
        'esac\n'
    )
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    result = subprocess.run(["sh", "-ec", _db_start_script()], env=env, capture_output=True, text=True)
    return result, calls.read_text().splitlines()


def test_an_existing_db_is_left_alone(tmp_path):
    result, calls = _run_db_start(tmp_path, "abc123")
    assert result.returncode == 0, result.stderr
    assert calls == ["compose ps -aq db"]


def test_a_failed_look_at_db_stops_rather_than_starting_it(tmp_path):
    result, calls = _run_db_start(tmp_path, "", ps_exit=1)
    assert result.returncode != 0
    assert calls == ["compose ps -aq db"]


def test_a_box_with_no_db_creates_it_before_the_backup(tmp_path):
    result, calls = _run_db_start(tmp_path, "")
    assert result.returncode == 0, result.stderr
    assert calls == ["compose ps -aq db", "compose up -d --wait db"]
    text = _release_deploy_text()
    assert text.index("--wait db") < text.index("bash -s -- pre-deploy")


@pytest.mark.parametrize("path", [".github/workflows/release.yml", ".github/workflows/rollback.yml"])
def test_no_compose_up_in_a_deploy_can_reach_db(path):
    """web's depends_on pulls db into a plain `up web`, so --no-deps is what keeps it out."""
    commands = _deploy_commands(path)
    assert commands
    for command in commands:
        if "--wait db" in command:
            assert '[ -n \\"\\$DB_ID\\" ] ||' in command, command
        else:
            assert re.search(r"docker compose up -d --no-deps( --remove-orphans)? web\b", command), command


def test_the_swap_names_every_service_but_db():
    """A new service has to join the swap's `up`, or a release would never start it."""
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    assert set(services) == {"db", "web"}


def test_the_guard_says_what_the_hash_cannot_promise():
    guard = (ROOT / "scripts/install_compose.sh").read_text()
    assert "exactly what `up -d` compares" not in guard
    assert "image label" in guard
    assert "changes the db service" in guard


@pytest.mark.criterion(5, "The other tenants keep working")
def test_the_role_touches_only_its_own_site_and_never_a_certificate():
    for task in _role_tasks():
        text = yaml.safe_dump(task)
        for runner in ("ansible.builtin.command", "ansible.builtin.shell"):
            assert "certbot" not in str(task.get(runner, "")), task["name"]
        for other in ("learn.seandesmet.com", "budget.seandesmet.com", "orbit-ops", "budget-buddy"):
            assert other not in text, task["name"]
    # Handlers are named apart from droplet-infra's own "Reload nginx", and test before reloading.
    handlers = yaml.safe_load((ROLE / "handlers/main.yml").read_text())
    assert [h["name"] for h in handlers] == ["Test the nginx config for apply", "Reload nginx for apply"]


def test_the_deploy_key_is_job_hunts_own_and_required():
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    # The shape the role asserts before it changes anything (tasks/main.yml).
    assert re.fullmatch(r"ssh-ed25519 \S+ github-actions job-hunt deploy", defaults["job_hunt_deploy_key"])
    orbit = (ROOT.parent / "orbit-ops/infra/ansible/roles/orbit_ops_app/defaults/main.yml")
    if orbit.exists():  # never another tenant's key
        key = re.search(r"ssh-ed25519 (\S+)", orbit.read_text()).group(1)
        assert key not in (ROLE / "defaults/main.yml").read_text()
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    names = [t["name"] for t in tasks]
    assert names.index("The deploy key is set") < names.index("Job Hunt's deploy key")


@pytest.mark.criterion(5, "The database is backed up off the droplet")
def test_dumps_land_where_backup_export_expects_them():
    """backup-export's contract (droplet-infra README): finished dumps are *.sql.gz in
    <app dir>/backups. The VM pulls them from there; the nightly unit makes them."""
    unit = (INFRA / "systemd/apply-backup.service").read_text()
    assert "WorkingDirectory=/opt/apply" in unit
    assert "ExecStart=/bin/bash /opt/apply/backup_db.sh nightly 14" in unit
    script = (ROOT / "scripts/backup_db.sh").read_text()
    assert "backups/" in script and ".sql.gz" in script
    timer = (INFRA / "systemd/apply-backup.timer").read_text()
    assert "OnCalendar=*-*-* 03:45:00 America/New_York" in timer


@pytest.mark.criterion(5, "Containers have memory limits")
def test_every_service_has_a_memory_limit():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    assert {name: s.get("mem_limit") for name, s in services.items() if not s.get("mem_limit")} == {}


def test_the_lxd_test_acts_only_on_apply_containers():
    lib = (INFRA / "lib.sh").read_text()
    assert '[[ $1 == apply-* ]] || die' in lib
    assert 'name="apply-app-' in (INFRA / "test.sh").read_text()
