"""Offline service checks, also used by npm run check and hosted CI."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    for args, cwd in [
        (["-m", "unittest", "discover", "-s", "tests", "-p", "*_test.py"], ROOT),
        (["tests/studio_board_selftest.py"], ROOT),
    ]:
        completed = subprocess.run([sys.executable, *args], cwd=cwd)
        if completed.returncode:
            return completed.returncode
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
