"""Print the affected build matrix. GitHub workflow inputs are passed in environment."""
import json
import os
import re
import subprocess
from pathlib import Path

games = json.loads(Path('config/games.json').read_text())
selected = os.environ.get('YY_GAME', '')
if selected:
    if selected not in games:
        raise SystemExit('Unknown requested game')
    affected = [selected]
else:
    before = os.environ.get('YY_BEFORE', '')
    if not re.fullmatch(r'[0-9a-f]{40}', before) or before == '0' * 40:
        affected = list(games)
    else:
        try:
            changed = subprocess.check_output(['git', 'diff', '--name-only', before, 'HEAD'], text=True).splitlines()
        except subprocess.CalledProcessError:
            changed = ['engine/']
        shared = any(p.startswith(('engine/', 'platform/', 'cmake/', 'config/', 'fastlane/')) or p in ('CMakeLists.txt', 'CMakePresets.json') or p == '.github/workflows/ios-testflight.yml' for p in changed)
        affected = [key for key, game in games.items() if shared or any(p.startswith(game['directory'] + '/') for p in changed)]
print(json.dumps({'game': affected}))
