"""Run the upstream board's selftest with an isolated three-checkout registry."""
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory() as folder:
        os.environ["LOCALAPPDATA"] = folder
        sys.path.insert(0, str(ROOT / "studio"))
        import studio_config
        registry = studio_config.agents_path()
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps({"remote": "origin", "branch": "main", "agents": {
            name: {"path": str(Path(folder) / name), "port": 45322 + i, "primary": i == 0}
            for i, name in enumerate(("A1", "A2", "A3"))}}))
        # The server's daemon refresh threads must exit before Windows removes
        # the fixture database; running the selftest in a child guarantees this.
        return subprocess.run([sys.executable, str(ROOT / "studio/fe_board.py"), "selftest"],
                              cwd=ROOT, env=os.environ.copy()).returncode

if __name__ == "__main__":
    raise SystemExit(main())
