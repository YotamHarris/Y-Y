"""Keep an email-protected Quick Tunnel available while the local board is healthy."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import xml.sax.saxutils as xml

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "studio"))
import fe_board
import manager_host

TASK = "YYEngine Board Sharing"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def validate(config):
    emails = config.get("allowed_mail", [])
    if not emails or any(not isinstance(e, str) or not re.fullmatch(r"[^\s@*,]+@[^\s@*,]+\.[^\s@*,]+", e) for e in emails):
        raise ValueError("Board sharing requires explicit allowed email addresses")
    if not Path(config["executable"]).is_file():
        raise ValueError("The configured cloudflared executable is missing")
    port = config.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid board port")
    return config


def command(config):
    validate(config)
    return [config["executable"], "tunnel", "--url", f"http://127.0.0.1:{config['port']}",
            "--http-host-header", "localhost", "--allowed-mail", ",".join(config["allowed_mail"])]


def matches(process, config):
    """Adopt/stop only this executable and this exact protected board command."""
    try:
        args = process.cmdline()
        return (os.path.normcase(process.exe()) == os.path.normcase(config["executable"])
                and args[1:4] == ["tunnel", "--url", f"http://127.0.0.1:{config['port']}"]
                and len(args) == 8 and args[4:7] == ["--http-host-header", "localhost", "--allowed-mail"]
                and {e.lower() for e in args[7].split(",")} == {e.lower() for e in config["allowed_mail"]})
    except psutil.Error:
        return False


def find(config):
    name = Path(config["executable"]).name.casefold()
    return next((p for p in psutil.process_iter(["name"])
                 if (p.info["name"] or "").casefold() == name and matches(p, config)), None)


def board_up(config):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{config['port']}/api/health", timeout=2) as response:
            return response.status == 200 and json.load(response).get("ok") is True
    except (OSError, ValueError):
        return False


def tunnel_url(process):
    try:
        for connection in process.net_connections(kind="tcp"):
            if connection.status != psutil.CONN_LISTEN or connection.laddr.ip not in ("127.0.0.1", "::1"):
                continue
            with urllib.request.urlopen(f"http://127.0.0.1:{connection.laddr.port}/quicktunnel", timeout=2) as response:
                host = json.load(response).get("hostname", "")
            if re.fullmatch(r"[a-z0-9-]+\.trycloudflare\.com", host):
                return "https://" + host
    except (psutil.Error, OSError, ValueError):
        pass
    return None


def spawn(config, folder):
    # Never pass coordinator credentials or TUNNEL_* environment overrides.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    log = folder / "tunnel.log"
    if log.exists() and log.stat().st_size > 2_000_000:
        log.replace(folder / "tunnel.previous.log")
    with log.open("ab") as output:
        child = subprocess.Popen(command(config), cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=output, creationflags=NO_WINDOW)
    return psutil.Process(child.pid)


def stop(process, config):
    if process and matches(process, config):
        process.terminate()
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            if matches(process, config):
                process.kill()


class Supervisor:
    def __init__(self, folder):
        self.folder, self.process, self.config = folder, None, None
        self.down_since, self.retry_at = None, 0

    def tick(self, config, healthy, now):
        validate(config)  # No public fallback if local configuration is broken.
        if self.config and self.config != config:
            stop(self.process, self.config)
            self.process = None
        self.config = config
        if self.process and not matches(self.process, config):
            self.process = None
        self.process = self.process or find(config)
        if not healthy:
            self.down_since = now if self.down_since is None else self.down_since
            if now - self.down_since >= 30:
                stop(self.process, config)
                self.process = None
            state = {"state": "waiting_for_board"}
        else:
            self.down_since = None
            if not self.process and now >= self.retry_at:
                self.retry_at = now + 30
                self.process = spawn(config, self.folder)
            url = tunnel_url(self.process) if self.process else None
            state = {"state": "running" if url else "connecting", "url": url}
        state.update(at=now, pid=self.process.pid if self.process else None)
        write(self.folder / "status.json", state)
        return state


def serve(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "supervisor.lock").open("a+b") as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"0")
            lock.flush()
        try:
            manager_host._lock(lock)
        except OSError:
            return  # Another launcher already watches this board.
        supervisor = Supervisor(folder)
        while True:
            try:
                config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
                if not config.get("enabled"):
                    stop(supervisor.process or find(config), config)
                    write(folder / "status.json", {"state": "disabled", "at": time.time()})
                    return
                supervisor.tick(config, board_up(config), time.time())
            except (OSError, ValueError, KeyError, psutil.Error) as error:
                if supervisor.config:
                    stop(supervisor.process, supervisor.config)
                    supervisor.process = None
                write(folder / "status.json", {"state": "error", "error": str(error)[:250], "at": time.time()})
            time.sleep(5)


def start(folder):
    if not (folder / "config.json").exists():
        return
    exe = Path(sys.executable).with_name("pythonw.exe") if os.name == "nt" else Path(sys.executable)
    with (folder / "supervisor.log").open("ab") as output:
        subprocess.Popen([str(exe), str(Path(__file__).resolve()), "serve", "--state-dir", str(folder)],
                         cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output, creationflags=NO_WINDOW)


def install(folder):
    who = subprocess.run(["whoami"], check=True, capture_output=True, text=True, creationflags=NO_WINDOW).stdout.strip()
    exe = Path(sys.executable).with_name("pythonw.exe")
    args = subprocess.list2cmdline([str(Path(__file__).resolve()), "serve", "--state-dir", str(folder)])
    repetition = '<Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>'
    content = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
 <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{xml.escape(who)}</UserId>{repetition}</LogonTrigger><TimeTrigger><Enabled>true</Enabled><StartBoundary>{time.strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary>{repetition}</TimeTrigger></Triggers>
 <Principals><Principal id="Author"><UserId>{xml.escape(who)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
 <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure></Settings>
 <Actions Context="Author"><Exec><Command>{xml.escape(str(exe))}</Command><Arguments>{xml.escape(args)}</Arguments><WorkingDirectory>{xml.escape(str(ROOT))}</WorkingDirectory></Exec></Actions>
</Task>'''
    path = folder / "scheduled-task.xml"
    path.write_text(content, encoding="utf-16")
    subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", str(path), "/F"], check=True, creationflags=NO_WINDOW)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("enable", "disable", "start", "status", "serve"))
    parser.add_argument("--allowed-mail", action="append")
    parser.add_argument("--cloudflared")
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args()
    if os.environ.get("FE_MANAGER_RUN"):
        raise SystemExit("Provider processes cannot configure board sharing")
    folder = args.state_dir or fe_board.board_dir() / "share"
    if args.action == "enable":
        if os.name != "nt":
            raise SystemExit("Automatic sharing installation requires Windows")
        exe = args.cloudflared or shutil.which("cloudflared")
        if not exe:
            raise SystemExit("Specify --cloudflared with the installed executable path")
        config = validate({"enabled": True, "executable": str(Path(exe).resolve()),
                           "port": fe_board.port(), "allowed_mail": args.allowed_mail or []})
        help_text = subprocess.run([config["executable"], "tunnel", "--help"], check=True,
                                  capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout
        if "--allowed-mail" not in help_text:
            raise SystemExit("Update cloudflared: this version lacks protected Quick Tunnels")
        write(folder / "config.json", config)
        install(folder)
        start(folder)
    elif args.action == "disable":
        config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        config["enabled"] = False
        write(folder / "config.json", config)
        stop(find(config), config)
        subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], check=True, creationflags=NO_WINDOW)
    elif args.action == "serve":
        serve(folder)
    elif args.action == "start":
        start(folder)
    else:
        path = folder / "status.json"
        print(path.read_text(encoding="utf-8") if path.exists() else "Board sharing is not running.")


if __name__ == "__main__":
    main()
