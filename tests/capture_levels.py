"""Refresh measured levels' opening fields and cards from the SDL app.

Run after scripts/build.ps1 through fe_manager.py gpu. These are staged
opening scenes at 390x844; gameplay touch evidence is in yy_tests.
"""
from pathlib import Path
import argparse
import os
import subprocess
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/figures/levels'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--levels', default='1,2,3,4,5,6,7,8,9,10')
    parser.add_argument('--cards', default='1,2,6,8,10')
    parser.add_argument('--exe', default=str(ROOT / 'build/windows/TapDemo.exe'))
    args = parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True, exist_ok=True)
    exe = Path(args.exe)
    for scene, prefix, levels in [('field', 'level', args.levels), ('instructions', 'card', args.cards)]:
        for level in filter(None, levels.split(',')):
            target = OUT / f'{prefix}-{int(level):02}.png'
            bmp = ROOT / 'build' / f'T35-{prefix}-{level}.bmp'
            env = dict(os.environ, YY_TAPDEMO_LEVEL=level, YY_TAPDEMO_SCENE=scene, YY_SCREENSHOT_PATH=str(bmp))
            run = subprocess.run([str(exe), '--smoke', '30'], cwd=exe.parent, env=env,
                                 capture_output=True, text=True, timeout=60, creationflags=0x08000000)
            if run.returncode or not bmp.exists():
                raise RuntimeError(f'{target.name}: exit {run.returncode}: {run.stdout} {run.stderr}')
            with Image.open(bmp) as raw:
                image = raw.convert('RGB')
                if image.size != (390, 844):
                    raise RuntimeError(f'{target.name}: expected 390x844, got {image.size}')
                image.save(target)
            bmp.unlink()
            print(f'{target.relative_to(ROOT)}: captured 390x844 {scene}', flush=True)


if __name__ == '__main__':
    main()
