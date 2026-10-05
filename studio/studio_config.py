"""Where the studio is, which game it serves, and that game's settings.

The studio (the board, the manager, sync, landing, the docs and the index) is
generic; the game it serves is described by one file at the game's root,
`studio.toml`. Every studio module finds the game, its data and its tools
through here, never through its own position on disk, so the studio can sit
in any game as a submodule (`studio/`) or, as before, in a game's tools
folder.

    repo_root()        the game's checkout: FE_REPO, else the nearest parent of
                       the studio holding studio.toml, else git's top level
    cfg()              studio.toml as a dict (empty when there is none)
    get('a.b', d)      one value by dotted path
    name(), owner()    the project's name and the person who directs it
    data_dir()         %LOCALAPPDATA%/<name>: the board, the manager, benches
    studio_dir()       this folder; studio_rel() the same relative to the repo
    tool(name)         a studio tool's path; tool_cmd(name) 'python <rel>/<name>'
    game_tools_dir()   the game's own tools (adapters are imported from there)
    artifacts_dir()    the game's scratch folder
    agents_path()      the agent registry (checkouts, ports)
    adapter(name)      the game module configured under [adapters], or None

Stdlib only, and cheap: it is imported by every hook.
"""
import importlib
import os
import subprocess
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = "studio.toml"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@lru_cache(maxsize=None)
def repo_root():
    env = os.environ.get("FE_REPO")
    if env:
        return Path(env).resolve()
    for d in HERE.parents:
        if (d / CONFIG).is_file():
            return d
    try:
        out = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--show-superproject-working-tree",
                              "--show-toplevel"], capture_output=True, text=True, timeout=10,
                             creationflags=NO_WINDOW).stdout.split()
        if out:
            return Path(out[0])
    except (OSError, subprocess.TimeoutExpired):
        pass
    return HERE.parent


@lru_cache(maxsize=None)
def cfg():
    try:
        with open(repo_root() / CONFIG, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def get(path, default=None):
    node = cfg()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def name():
    return get("name") or repo_root().name


def owner():
    return get("owner") or "the owner"


def local_appdata():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def data_dir():
    return local_appdata() / name()


def studio_dir():
    return HERE


def rel(path):
    """`path` relative to the repo, with forward slashes, when it is inside it."""
    try:
        return Path(path).resolve().relative_to(repo_root()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def studio_rel():
    return rel(HERE)


def tool(name, root=None):
    """A studio tool's path, in this tree or in the checkout `root`."""
    return (Path(root) / studio_rel() if root else HERE) / name


def tool_cmd(name):
    return f"python {studio_rel()}/{name}"


def studio_in(checkout):
    """The studio folder inside another checkout of the same game: the
    configured place first, then any legacy place (a checkout not yet moved)."""
    for place in [studio_rel(), *get("studio.legacy_dirs", [])]:
        p = Path(checkout) / place
        if (p / "fe_board.py").is_file():
            return p
    return Path(checkout) / studio_rel()


def game_tools_dir():
    return repo_root() / get("game_tools", "tools")


def artifacts_dir():
    return repo_root() / get("artifacts", ".studio")


def agents_path():
    if get('routing.registry_shared', False):
        return data_dir() / 'studio.agents.json'
    return repo_root() / get("agents", "studio.agents.json")


@lru_cache(maxsize=None)
def adapter(kind):
    """The game's module for `kind` (gpu, debt, ...), or None when the game has
    none. A configured module that fails to import raises: a game that asked for
    a GPU lease must not run without one."""
    module = get(f"adapters.{kind}")
    if not module:
        return None
    tools = str(game_tools_dir())
    if tools not in sys.path:
        sys.path.append(tools)
    return importlib.import_module(module)


def game_module(module):
    """A module of the game's own tools by name (a [[nightly]] job's, D262). Like adapter(),
    a configured module that fails to import raises."""
    tools = str(game_tools_dir())
    if tools not in sys.path:
        sys.path.append(tools)
    return importlib.import_module(module)
