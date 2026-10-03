import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ToolingTest(unittest.TestCase):
    def test_game_scaffold_registers_separate_code_assets_and_app_identity(self):
        with tempfile.TemporaryDirectory(prefix='yy-scaffold-') as temporary:
            root = Path(temporary)
            (root / 'scripts').mkdir()
            (root / 'config').mkdir()
            shutil.copy(ROOT / 'scripts/new-game.py', root / 'scripts/new-game.py')
            shutil.copy(ROOT / 'config/games.json', root / 'config/games.json')
            shutil.copytree(ROOT / 'games/tapdemo', root / 'games/tapdemo')
            command = [sys.executable, str(root / 'scripts/new-game.py'), 'puzzle', '--target', 'Puzzle', '--bundle-id', 'com.example.puzzle']
            subprocess.run(command, check=True, capture_output=True, text=True)
            game = json.loads((root / 'config/games.json').read_text())['puzzle']
            self.assertEqual(game['bundleId'], 'com.example.puzzle')
            self.assertEqual(game['target'], 'Puzzle')
            self.assertIn('namespace puzzle', (root / 'games/puzzle/src/model.cpp').read_text())
            self.assertTrue((root / 'games/puzzle/assets/spark.bmp').exists())
            self.assertTrue((root / 'games/puzzle/include/puzzle/model.hpp').exists())
            duplicate = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(duplicate.returncode, 0)

    def test_distribution_selector_accepts_only_registered_games(self):
        env = dict(os.environ, YY_GAME='tapdemo')
        script = str(ROOT / 'scripts/affected-games.py')
        result = subprocess.check_output([sys.executable, script], cwd=ROOT, env=env, text=True)
        self.assertEqual(json.loads(result), {'game': ['tapdemo']})
        env['YY_GAME'] = '../unknown'
        result = subprocess.run([sys.executable, script], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_initial_push_builds_all_registered_games(self):
        env = dict(os.environ, YY_GAME='', YY_BEFORE='0' * 40)
        result = subprocess.check_output([sys.executable, str(ROOT / 'scripts/affected-games.py')], cwd=ROOT, env=env, text=True)
        games = json.loads((ROOT / 'config/games.json').read_text())
        self.assertEqual(json.loads(result)['game'], list(games))

if __name__ == '__main__':
    unittest.main()
