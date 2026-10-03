"""Select an installed stable Xcode meeting the project's Apple SDK baseline."""
from pathlib import Path
import os
import plistlib
import re

candidates = {}
for app in Path('/Applications').glob('Xcode*.app'):
    if re.search(r'beta|\bRC\d*\b|_RC', app.name, re.I):
        continue
    try:
        with (app / 'Contents/Info.plist').open('rb') as handle:
            info = plistlib.load(handle)
        version = tuple(int(part) for part in info['CFBundleShortVersionString'].split('.'))
        if version[0] >= 26:
            candidates[app.resolve()] = version
    except (OSError, ValueError, KeyError):
        continue
if not candidates:
    raise SystemExit('This macOS runner needs an installed stable Xcode 26+. Update the runner image.')
selected = max(candidates, key=candidates.get)
developer = selected / 'Contents/Developer'
with Path(os.environ['GITHUB_ENV']).open('a') as output:
    output.write(f'DEVELOPER_DIR={developer}\n')
print('Selected', selected.name, 'version', '.'.join(map(str, candidates[selected])))
