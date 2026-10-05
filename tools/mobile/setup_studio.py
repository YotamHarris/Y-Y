"""Configure native Agent Studio locally, reusing existing Discord credentials."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

from yy_mobile import ROOT, env_file, git
sys.path.insert(0, str(ROOT / "studio"))
import manager_host as host
import studio_config


def configure(args):
    env = env_file(ROOT / ".env")
    config = host.load_config() if host.config_path().exists() else {}
    config["repo"] = str(ROOT)
    config.setdefault("created", time.time())
    ids = [x.strip() for x in env.get("DISCORD_USER_IDS", "").split(",") if x.strip()]
    config["guild_id"] = args.guild or env.get("DISCORD_GUILD_ID") or config.get("guild_id", "")
    config["channel_id"] = args.channel or env.get("YY_DISCORD_CHANNEL_ID") or config.get("channel_id", "")
    config["owner_id"] = args.owner or (ids[0] if ids else config.get("owner_id", ""))
    config["owner_ids"] = list(dict.fromkeys([config["owner_id"], *(ids or config.get("owner_ids", []))]))
    if not config["channel_id"] and (ROOT / ".yy/tasks.sqlite").exists():
        import sqlite3
        with sqlite3.connect(f"file:{(ROOT / '.yy/tasks.sqlite').as_posix()}?mode=ro", uri=True) as db:
            channels = [r[0] for r in db.execute("SELECT DISTINCT json_extract(data,'$.channelId') FROM tasks WHERE json_extract(data,'$.channelId')<>'multica'")]
            if len(channels) == 1:
                config["channel_id"] = channels[0]
    for key in ("guild_id", "channel_id", "owner_id"):
        if not str(config[key]).isdigit():
            raise ValueError(f"Specify --{ {'guild_id':'guild','channel_id':'channel','owner_id':'owner'}[key]} with the Discord ID")
    if any(not x.isdigit() for x in config["owner_ids"]):
        raise ValueError("Developer allowlist contains an invalid Discord ID")
    if env.get("YY_DISCORD_TALK_CHANNEL_ID"):
        config["talk_channel_id"] = env["YY_DISCORD_TALK_CHANNEL_ID"]
    for provider in ("codex", "claude"):
        configured = env.get(f"YY_{provider.upper()}_PATH") or config.get(provider) or provider
        exe = shutil.which(configured) or (configured if Path(configured).is_file() else "")
        if not exe or Path(exe).suffix.lower() in (".cmd", ".bat"):
            raise ValueError(f"Configure a native {provider} executable; shell shims are unsupported")
        config[provider] = exe
    config.setdefault("nightly_crew", "off")
    config.setdefault("usage_report", False)
    if env.get("DISCORD_TOKEN") and not host.credential_read():
        host.credential_write(env["DISCORD_TOKEN"])
    host.config_path().parent.mkdir(parents=True, exist_ok=True)
    host.config_path().write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    registry = {"remote": "origin", "branch": "main", "agents": {
        "A1": {"path": str(ROOT), "primary": True, "port": 45321}}}
    existing = json.loads(studio_config.agents_path().read_text()) if studio_config.agents_path().exists() else {}
    for name, port in (("A2", 45322), ("A3", 45323)):
        path = Path(existing.get("agents", {}).get(name, {}).get("path") or ROOT / ".yy/studio-workers" / name)
        if not (path / ".git").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            git(ROOT, "worktree", "add", "--detach", str(path), "origin/main")
        registry["agents"][name] = {"path": str(path), "port": port}
    studio_config.agents_path().parent.mkdir(parents=True, exist_ok=True)
    studio_config.agents_path().write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    print("Agent Studio configured. Discord credentials are in Windows Credential Manager; two mobile worker checkouts are registered.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for key in ("guild", "channel", "owner"):
        parser.add_argument("--" + key)
    configure(parser.parse_args())
