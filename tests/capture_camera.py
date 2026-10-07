"""T31 actual SDL readbacks at 390x844; run through fe_manager.py gpu.

Aim scenes pinch/hold/cancel/launch through the Game's pointer handlers on level 6,
frozen at each beat; a launch eases back to the pre-aim view.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/figures/shot-camera'
BEATS = ['wide', 'aim-early', 'aim', 'cancel-early', 'cancel',
         'launch-early', 'launch']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', default=str(ROOT / 'build/windows/TapDemo.exe'))
    args = parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True, exist_ok=True)
    exe = Path(args.exe)
    captures = []
    for index, beat in enumerate(BEATS, 1):
        bmp = OUT / f'{index}-{beat}.bmp'
        bmp.unlink(missing_ok=True)
        env = dict(os.environ, YY_TAPDEMO_LEVEL='6', YY_TAPDEMO_SCENE=f'camera-{beat}',
                   YY_SCREENSHOT_PATH=str(bmp))
        result = subprocess.run([str(exe), '--smoke', '10'], cwd=exe.parent, env=env,
                                capture_output=True, text=True, timeout=60,
                                creationflags=0x08000000)
        if result.returncode or not bmp.exists():
            raise RuntimeError(f'{beat}: exit {result.returncode}: {result.stdout} {result.stderr}')
        with Image.open(bmp) as source:
            shot = source.convert('RGB')
        if shot.size != (390, 844):
            raise RuntimeError(f'{beat}: expected 390x844, got {shot.size}')
        shot.save(bmp.with_suffix('.png'))
        bmp.unlink()
        captures.append({'file': bmp.with_suffix('.png').name, 'scene': f'camera-{beat}',
                         'size': list(shot.size), 'source': 'SDL render readback',
                         'fixture': 'level 6 touch pinch/hold/cancel/launch'})
        print(f'{beat}: actual SDL readback, 390x844', flush=True)
    # Exact Camera restoration is checked by shotCameraChecks. Decorative
    # glows evolve during the gesture, so entire frames need not match bytes.
    (OUT / 'captures.json').write_text(json.dumps(captures, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
