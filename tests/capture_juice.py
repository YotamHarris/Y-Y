"""T23 hit-feedback captures at phone size (390x844). Run through fe_manager.py gpu, after scripts/build.ps1.

Each is a staged scene (YY_TAPDEMO_SCENE): juice-break (chips and a nudge mid-break), juice-bomb (a bomb's blast mid-way),
juice-fog (the fog peeling off cells a break uncovered, with a power-up and the goal found there) and garden-damage (a row
showing whole, cracked and badly cracked bricks). A real sling starts each; the game is stepped to the moment and frozen,
and the pixels are read back from the running SDL app.
"""
from pathlib import Path
import argparse
import os
import subprocess
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/figures/juice'
SHOTS=[('1-break','juice-break'),('2-bomb','juice-bomb'),('3-fog-lift','juice-fog'),('4-cracks','garden-damage')]


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--level',default='1'); parser.add_argument('--exe',default=str(ROOT/'build/windows/TapDemo.exe'))
    args=parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True,exist_ok=True)
    exe=Path(args.exe)
    for name,scene in SHOTS:
        bmp=OUT/(name+'.bmp')
        env=dict(os.environ,YY_TAPDEMO_LEVEL=args.level,YY_TAPDEMO_SCENE=scene,YY_SCREENSHOT_PATH=str(bmp))
        p=subprocess.run([str(exe),'--smoke','10'],cwd=exe.parent,env=env,capture_output=True,text=True,timeout=60,creationflags=0x08000000)
        if p.returncode or not bmp.exists(): raise RuntimeError(f'{name}: exit {p.returncode}: {p.stdout} {p.stderr}')
        image=Image.open(bmp).convert('RGB'); image.save(OUT/(name+'.png')); bmp.unlink()
        print(f'{name}: {scene} captured, {image.size[0]}x{image.size[1]}',flush=True)


if __name__=='__main__': main()
