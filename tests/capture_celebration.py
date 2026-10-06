"""T20 goal celebration beats at phone size. Run through fe_manager.py gpu, after scripts/build.ps1.

Each beat is a staged scene (YY_TAPDEMO_SCENE=won-approach, won-burst, won-goal, won-card): a real sling
into the goal, the game stepped to the beat and frozen, and the pixels read back from the running SDL app.
"""
from pathlib import Path
import argparse
import os
import subprocess
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/figures/celebration'
BEATS=[('1-approach','won-approach'),('2-burst','won-burst'),('3-goal','won-goal'),('4-card','won-card')]


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--level',default='1'); args=parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True,exist_ok=True)
    for name,scene in BEATS:
        bmp=OUT/(name+'.bmp')
        env=dict(os.environ,YY_TAPDEMO_LEVEL=args.level,YY_TAPDEMO_SCENE=scene,YY_SCREENSHOT_PATH=str(bmp))
        p=subprocess.run([str(ROOT/'build/windows/TapDemo.exe'),'--smoke','10'],cwd=ROOT/'build/windows',
                         env=env,capture_output=True,text=True,timeout=60,creationflags=0x08000000)
        if p.returncode or not bmp.exists(): raise RuntimeError(f'{name}: exit {p.returncode}: {p.stdout} {p.stderr}')
        image=Image.open(bmp).convert('RGB'); image.save(OUT/(name+'.png')); bmp.unlink()
        print(f'{name}: {scene} captured, {image.size[0]}x{image.size[1]}',flush=True)


if __name__=='__main__': main()
