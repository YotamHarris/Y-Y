"""Who this checkout is: its agent name and control port (D165, D194).

A session's FE_AGENT and FE_CONTROL_PORT come from the checkout's
.claude/settings.local.json, which Claude Code reads once, at session start.
A session routed to another checkout (D165) keeps the first checkout's pair
until it restarts, so a tool that trusted them would build into the other
agent's scratch target and talk to the other agent's instance. The registry
decides instead: the checkout this file sits in is who you are.

    agent_name()          the checkout's name, else FE_AGENT, else 'local'
    control_port(default) the checkout's port when FE_AGENT names another
                          registered agent (a moved session), else
                          FE_CONTROL_PORT, else the checkout's port, else default

FE_CONTROL_PORT still overrides when FE_AGENT agrees with the checkout or
names no registered agent (a marker such as fe_model's 'model-verify'), so a
script that picks its own port keeps it. The game applies the same rule
(BodySimulation: control.rs, `Control::port_from_env`). The registry and the
default port come from studio.toml (`agents`, `[game] control_port`). Stdlib only.
"""
import json
import os
from pathlib import Path

import studio_config

HERE = Path(__file__).resolve().parent      # the studio's folder
REPO = studio_config.repo_root()            # checkout root


def norm(path):
    return str(path).replace("\\", "/").rstrip("/").lower()


def registry():
    try:
        return json.loads(studio_config.agents_path().read_text(encoding="utf-8")).get("agents", {})
    except (OSError, json.JSONDecodeError):
        return {}


def checkout(path=REPO):
    """(name, entry) of the registered checkout holding `path`, or (None, None)."""
    here = norm(path)
    for name, agent in registry().items():
        root = norm(agent.get("path", ""))
        if root and (here == root or here.startswith(root + "/")):
            return name, agent
    return None, None


def moved():
    """(stale FE_AGENT, this checkout's name) when the session started in
    another registered checkout, else None."""
    name, _ = checkout()
    env = os.environ.get("FE_AGENT")
    if name and env and env != name and env in registry():
        return env, name
    return None


def agent_name():
    name, _ = checkout()
    return name or os.environ.get("FE_AGENT") or "local"


def control_port(default=None):
    if default is None:
        default = studio_config.get("game.control_port", 0)
    name, agent = checkout()
    if name and moved():
        return int(agent.get("port", default))
    env = os.environ.get("FE_CONTROL_PORT", "").strip()
    if env.isdigit():
        return int(env)
    if name:
        return int(agent.get("port", default))
    return default
