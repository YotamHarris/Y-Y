"""Create a compiled game project from TapDemo and register it in the build matrix."""
import argparse
import json
from pathlib import Path
import re
import shutil

parser = argparse.ArgumentParser()
parser.add_argument('id', help='Lowercase C++ identifier, e.g. puzzle')
parser.add_argument('--target', required=True, help='Executable/scheme name, e.g. Puzzle')
parser.add_argument('--bundle-id', required=True, help='Unique Apple app identifier')
args = parser.parse_args()
if not re.fullmatch(r'[a-z][a-z0-9_]*', args.id) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', args.target) or not re.fullmatch(r'[A-Za-z0-9.-]+', args.bundle_id):
    parser.error('Invalid game ID, target, or bundle ID')
root = Path(__file__).resolve().parents[1]
config = root / 'config/games.json'
games = json.loads(config.read_text())
destination = root / 'games' / args.id
if args.id in games or destination.exists() or any(g['target'] == args.target or g['bundleId'] == args.bundle_id for g in games.values()):
    parser.error('Game, target, or bundle ID already exists')
shutil.copytree(root / 'games/tapdemo', destination)
(destination / 'include/tapdemo').rename(destination / 'include' / args.id)
for path in destination.rglob('*'):
    if path.suffix in ('.cpp', '.hpp'):
        content = path.read_text().replace('tapdemo', args.id).replace('TAP DEMO', args.target.upper())
        path.write_text(content)
games[args.id] = {'target': args.target, 'directory': f'games/{args.id}', 'bundleId': args.bundle_id, 'version': '0.1.0', 'internalGroup': 'YY Internal'}
config.write_text(json.dumps(games, indent=2) + '\n')
print(f'Created {args.id}. Reconfigure CMake and set up GitHub environment testflight-{args.id}. Select mobile.game in studio.toml before assigning its manager tasks.')
