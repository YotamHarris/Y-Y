"""YYEngine's trusted landing and TestFlight adapter for Agent Studio.

Imported by the supervisor's pushed release. Candidate checkouts cannot replace
this module, its scope policy, or the validation scripts it invokes for this run.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "studio"))
import studio_config

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BUILD = re.compile(r"^(?:(?:make|create|upload|build|send|give me|please)\s+)?(?:a\s+)?(?:new\s+)?(?:ios\s+)?testflight(?:\s+build)?[.!]?$", re.I)


def provider_environment(source):
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
               "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG", "LC_ALL", "CODEX_HOME", "CLAUDE_CONFIG_DIR",
               "CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CODE_OAUTH_TOKEN", "PYTHONUTF8"}
    return {k: v for k, v in source.items() if k.upper() in allowed}


def git(repo, *args, check=True):
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, creationflags=NO_WINDOW,
                       env={**provider_environment(os.environ), "GIT_TERMINAL_PROMPT": "0"})
    if check and p.returncode:
        raise RuntimeError(f"git {args[0]} failed: {(p.stderr or p.stdout)[-2000:]}")
    return p.stdout.strip() if check else p


def maintenance_paths():
    """The owner-approved paths in the supervisor's release, not candidate settings."""
    with (ROOT / "studio.toml").open("rb") as file:
        paths = tomllib.load(file).get("mobile", {}).get("maintenance_paths", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p or
            not re.fullmatch(r"[A-Za-z0-9_.\-/]+", p) or p.startswith("/") or
            any(part in (".", "..", "") for part in p.rstrip("/").split("/")) for p in paths):
        raise ValueError("Invalid trusted mobile maintenance paths")
    return paths


def worker_edit_rules():
    """Pre-authorize local file edits only; shell and publication permissions stay unchanged."""
    return [f"Edit(/{p}{'**' if p.endswith('/') else ''})" for p in maintenance_paths()]


def assert_scope(paths, game_directory):
    maintenance = maintenance_paths()
    for path in paths:
        if not path or "\\" in path or path.startswith("/") or any(p in (".", "..", "") for p in path.split("/")):
            raise ValueError(f"Invalid candidate path: {path}")
        if path.lower().startswith("build/") or path.lower().endswith((".p12", ".p8", ".key", ".mobileprovision", ".ipa")) or any(
                p.lower() in (".yy", ".env") or p.lower().startswith(".env.") for p in path.split("/")):
            raise ValueError(f"Secret or output in candidate: {path}")
        if not path.startswith(("engine/", game_directory + "/", "tests/")) and not any(
                path.startswith(p) if p.endswith("/") else path == p for p in maintenance):
            raise ValueError(f"Task changed {path}; allowed: engine/, {game_directory}/, tests/, "
                             "and owner-approved CI/build and project instruction paths")


def validate(repo, heartbeat):
    """Execute trusted scripts with candidate source and no service credentials."""
    import manager_host
    trusted = Path(manager_host.load_config()["repo"])
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", str(ROOT / "scripts/build.ps1"), "-RepoRoot", str(repo),
           "-ToolsRoot", str(trusted), "-Smoke"]
    log = studio_config.artifacts_dir() / "mobile"
    log.mkdir(parents=True, exist_ok=True)
    file = log / (uuid.uuid4().hex + ".log")
    started = time.monotonic()
    with file.open("w", encoding="utf-8") as out:
        process = subprocess.Popen(cmd, cwd=repo, env=provider_environment(os.environ), stdout=out,
                                   stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        while True:
            try:
                code = process.wait(timeout=20)
                break
            except subprocess.TimeoutExpired:
                heartbeat()
                if time.monotonic() - started > 1200:
                    import manager_workers
                    manager_workers.end_tree(process)
                    raise RuntimeError(f"Mobile build timed out; see {file}")
    if code:
        raise RuntimeError(f"Mobile validation failed; see {file}:\n" + file.read_text(encoding="utf-8")[-3000:])
    return f"Release build, C++ tests and 120-frame rendering smoke passed. Log: {file}"


def land(run, result, heartbeat=lambda: None):
    """Serialize integration; validate again after a rebase and push only that HEAD."""
    if run["role"] != "worker" or result.get("status") != "complete":
        return result
    import manager_host
    import manager_workers
    repo = Path(run["cwd"])
    original = git(repo, "rev-parse", "HEAD")
    scratch = studio_config.data_dir() / "integration" / run["id"]
    scratch.parent.mkdir(parents=True, exist_ok=True)
    # A separate OS lock serializes publication across both worker processes.
    # Use a distinct file rather than the supervisor's singleton lock.
    lock = studio_config.data_dir() / "integration.lock"
    try:
        with lock.open("a+b") as handle:
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 2400
            while True:
                try:
                    manager_host._lock(handle)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Another mobile publication has held the integration lock for 40 minutes")
                    heartbeat()
                    time.sleep(5)
            if git(repo, "status", "--porcelain"):
                raise ValueError("Candidate checkout is dirty; commit the game changes first")
            if result.get("head") != original:
                raise ValueError("Worker evidence does not name the current HEAD")
            checks = result.get("checks") or []
            if len(checks) != len(manager_workers.CHECKS) or {c["name"] for c in checks} != set(manager_workers.CHECKS):
                raise ValueError("Worker result is missing required mobile evidence")
            if not all(manager_workers.check_passes(c, original) for c in checks if c["name"] != "tests"):
                raise ValueError("Player, visual or device evidence is incomplete")
            game = studio_config.get("mobile.game")
            directory = json.loads((ROOT / "config/games.json").read_text())[game]["directory"]
            git(repo, "fetch", "origin", "main")
            paths = git(repo, "diff", "--name-only", "--no-renames", "-z", "origin/main...HEAD").split("\0")
            assert_scope([p for p in paths if p], directory)
            if git(repo, "merge-base", "--is-ancestor", "HEAD", "origin/main", check=False).returncode == 0:
                if manager_workers.evidence_passes(result, original):
                    return result  # recover a successful publication whose result was lost
                raise ValueError("Already-published candidate lacks matching evidence")
            # Reject symbolic links, including links escaping the candidate checkout.
            for path in paths:
                if path and git(repo, "ls-tree", "HEAD", "--", path).startswith("120000 "):
                    raise ValueError(f"Game task introduced a symlink: {path}")
            git(repo, "worktree", "add", "--detach", str(scratch), original)
            for attempt in range(3):
                git(scratch, "fetch", "origin", "main")
                if git(scratch, "merge-base", "--is-ancestor", "HEAD", "origin/main", check=False).returncode == 0:
                    raise ValueError("Candidate is already on main; no new publication was performed")
                git(scratch, "rebase", "origin/main")
                assert_scope([p for p in git(scratch, "diff", "--name-only", "--no-renames", "-z", "origin/main...HEAD").split("\0") if p], directory)
                evidence = validate(scratch, heartbeat)
                head = git(scratch, "rev-parse", "HEAD")
                if git(scratch, "status", "--porcelain"):
                    raise ValueError("Validation changed tracked source files")
                if git(repo, "rev-parse", "HEAD") != original or git(repo, "status", "--porcelain"):
                    raise ValueError("Worker checkout changed during validation")
                pushed = git(scratch, "push", "origin", "HEAD:main", check=False)
                if pushed.returncode == 0:
                    git(repo, "fetch", "origin", "main")
                    if git(repo, "rev-parse", "HEAD") != original or git(repo, "status", "--porcelain"):
                        raise ValueError(f"Published {head}, but the worker checkout changed; preserved it for recovery")
                    git(repo, "reset", "--hard", head)
                    updated = [dict(c, outcome="passed", command="scripts/build.ps1 -Smoke (trusted supervisor)", detail=evidence)
                               if c["name"] == "tests" else c for c in checks]
                    return dict(result, head=head, checks=updated, summary=result["summary"] +
                                "\n\nLanded by the mobile manager. TestFlight delivery is tracked separately.")
                if "rejected" not in pushed.stderr and "non-fast-forward" not in pushed.stderr:
                    raise RuntimeError("Coordinator push failed: " + pushed.stderr[-2000:])
            raise RuntimeError("Main changed during three publication attempts; candidate remains local")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return dict(result, status="continue", summary=f"Mobile coordinator stopped: {error}\n" + result.get("summary", ""))
    finally:
        if scratch.exists():
            git(repo, "worktree", "remove", "--force", str(scratch), check=False)


def env_file(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^\s*([A-Z_][A-Z_0-9]*)\s*=\s*(.*)$", line)
            if match:
                value = match[2].strip()
                if value.startswith(('"', "'")) and value.endswith(value[0]):
                    value = value[1:-1]
                values[match[1]] = value
    return values


class GitHub:
    def __init__(self, repo_root):
        env = env_file(Path(repo_root) / ".env")
        self.token = os.environ.get("GITHUB_TOKEN") or env.get("GITHUB_TOKEN")
        self.repository = studio_config.get("mobile.repository")
        if not self.token:
            raise ValueError("Set GITHUB_TOKEN in the coordinator's ignored .env for TestFlight status and dispatch")

    def api(self, path, payload=None):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "YYEngine-Agent-Studio"}
        request = urllib.request.Request(f"https://api.github.com/repos/{self.repository}{path}",
                                         data=json.dumps(payload).encode() if payload is not None else None,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"GitHub request failed (HTTP {error.code})") from None

    def runs(self, sha):
        workflow = studio_config.get("mobile.workflow", "ios-testflight.yml")
        return self.api(f"/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100")["workflow_runs"]

    def state(self, run, game):
        jobs = self.api(f"/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
        job = next((j for j in jobs if j["name"] == f"TestFlight ({game})"), None)
        if job and job.get("status") == "completed" and job.get("conclusion") != "success":
            return "failed"
        if job and job.get("conclusion") == "success" and any(
                step["name"] == "Verify TestFlight readiness" and step.get("conclusion") == "success"
                for step in job.get("steps", [])):
            return "ready"
        if run.get("status") == "completed":
            # Successful pushes may select no games or only other games. Require
            # selector and matrix evidence before treating delivery as unnecessary.
            selected = any(j["name"] == "select" and j.get("conclusion") == "success" for j in jobs)
            excluded = any((j["name"] == "TestFlight (${{ matrix.game }})" and
                            j.get("conclusion") == "skipped") or
                           (j["name"].startswith("TestFlight (") and j["name"].endswith(")") and
                            j["name"] != "TestFlight (${{ matrix.game }})") for j in jobs)
            if not job and run.get("event") == "push" and run.get("conclusion") == "success" and selected and excluded:
                return "not_applicable"
            return "failed"  # workflow success alone never establishes tester readiness
        if job and any(s["name"] == "Upload build" and s.get("conclusion") == "success" for s in job.get("steps", [])):
            return "processing"
        return "building" if job and job.get("status") == "in_progress" else "waiting_build"


def delivery_key(item):
    return "mobile.delivery:" + item


def queue_build(b, event_id, game=None, sha=None):
    import manager_store as store
    game = game or studio_config.get("mobile.game")
    if game not in json.loads((ROOT / "config/games.json").read_text()):
        raise ValueError("Unknown game")
    key = delivery_key(event_id)
    if store.setting(b, key):
        return False
    item = {"game": game, "sha": sha, "request": "studio-" + uuid.uuid4().hex, "state": "queued", "explicit": True}
    with b.tx():
        store.set_setting(b, key, json.dumps(item))
        store.notify(b, key + ":queued", f"TestFlight build queued for {game}. Signing and upload run on GitHub's hosted Mac.")
    return True


def build_message(b, text, event_id):
    if not BUILD.fullmatch(text.strip()):
        return False
    queue_build(b, event_id)
    return True


def view(b):
    return {"game": studio_config.get("mobile.game"), "deliveries": [
        {k: item[k] for k in ("game", "sha", "task", "state", "url", "error") if k in item}
        for item in (json.loads(row["value"]) for row in b.q(
            "SELECT value FROM pm_settings WHERE key LIKE 'mobile.delivery:%' ORDER BY rowid DESC LIMIT 15"))]}


def tick(b, config, client=None, now=None):
    """Persist dispatch intent before networking; recover ambiguous dispatch by run identity."""
    import manager_store as store
    now = time.time() if now is None else now
    last = float(store.setting(b, "mobile.poll", "0"))
    if now - last < 60:
        return
    store.set_setting(b, "mobile.poll", now)
    # Every landed revision gets its own delivery record, including accepted tasks.
    for task in b.q("SELECT task_id,landed_head FROM pm_tasks WHERE landed_head IS NOT NULL AND landed_head<>''"):
        key = delivery_key(f"T{task['task_id']}:{task['landed_head']}")
        if not store.setting(b, key):
            store.set_setting(b, key, json.dumps({"game": studio_config.get("mobile.game"),
                              "sha": task["landed_head"], "task": task["task_id"], "state": "waiting_workflow"}))
    pending = b.q("SELECT key,value FROM pm_settings WHERE key LIKE 'mobile.delivery:%'")
    for row in pending:
        item = json.loads(row["value"])
        # Reclassify cached failures once after introducing intentional skips.
        if item["state"] in ("ready", "not_applicable") or (item["state"] == "failed" and item.get("state_version") == 1):
            continue
        try:
            client = client or GitHub(config["repo"])
            if not item.get("sha"):
                git(config["repo"], "fetch", "origin", "main")
                item["sha"] = git(config["repo"], "rev-parse", "origin/main")
                store.set_setting(b, row["key"], json.dumps(item))
            runs = client.runs(item["sha"])
            matching = [r for r in runs if r.get("head_sha") == item["sha"] and
                        (r.get("display_title", "").endswith(item["request"]) if item.get("explicit") else r.get("event") == "push")]
            if matching:
                run = max(matching, key=lambda r: r["id"])
                item.update(run=run["id"], url=run["html_url"])
                state = client.state(run, item["game"])
                item["state_version"] = 1
            elif item["state"] == "queued":
                # Never repeat this POST automatically after an unknown outcome.
                item["state"] = "dispatching"
                store.set_setting(b, row["key"], json.dumps(item))
                client.api(f"/actions/workflows/{studio_config.get('mobile.workflow')}/dispatches", {
                    "ref": "main", "inputs": {"sha": item["sha"], "game": item["game"], "task_id": item["request"]}})
                state = "dispatching"
            else:
                state = item["state"]
            if state != item["state"] or item.get("reported") != state:
                detail = {"ready": "Apple processing, tester assignment and internal testing readiness verified.",
                          "failed": "Delivery failed; inspect the workflow. The game commit remains landed.",
                          "processing": "Uploaded; Apple processing and tester readiness are still pending.",
                          "building": "Hosted Mac build and signing are running.",
                          "waiting_build": "Waiting for workflow checks and the game build job.",
                          "not_applicable": "No game changes require a TestFlight build; the workflow skipped delivery for this game.",
                          "dispatching": "Build dispatch recorded; waiting for GitHub to expose its run.",
                          "waiting_workflow": "Waiting for the main-push TestFlight workflow."}.get(state, state)
                message = f"TestFlight {item['game']} @ {item['sha'][:12]}: {detail}" + ("\n" + item["url"] if item.get("url") else "")
                with b.tx():
                    store.notify(b, row["key"] + ":" + state, message, task=item.get("task"), ping=state in ("ready", "failed"))
                    b.add_message("manager", "owner", "TestFlight " + state, message, kind="fyi",
                                  topic=f"T{item['task']}" if item.get("task") else None)
                item["reported"] = state
            item["state"] = state
            item.pop("error", None)
        except (OSError, ValueError, RuntimeError) as error:
            # Network errors preserve the delivery identity and never enqueue implementation.
            message = str(error)[:250]
            if item.get("error") != message:
                store.notify(b, row["key"] + ":attention:" + str(int(now)), "TestFlight tracking needs attention: " + message,
                             task=item.get("task"), ping=True)
            item["error"] = message
        store.set_setting(b, row["key"], json.dumps(item))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("build", "status"))
    ap.add_argument("--game")
    args = ap.parse_args()
    if os.environ.get("FE_MANAGER_RUN"):
        raise SystemExit("Provider processes cannot dispatch or control TestFlight delivery")
    import fe_board
    import manager_host
    with fe_board.Board() as b:
        if args.action == "build":
            queue_build(b, "local:" + uuid.uuid4().hex, args.game)
            tick(b, manager_host.load_config(), now=time.time() + 61)
        for row in b.q("SELECT value FROM pm_settings WHERE key LIKE 'mobile.delivery:%'"):
            print(json.dumps(json.loads(row["value"]), indent=2))


if __name__ == "__main__":
    main()
