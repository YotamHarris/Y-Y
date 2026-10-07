"""T26 captures at phone size (390x844). Run through fe_manager.py gpu, after scripts/build.ps1.

Each is a staged scene (YY_TAPDEMO_SCENE) on a pinned level: glint (the camera on the hidden goal, the fogged cells that shimmer
around it and the way to the nearest open cell; levels 4 and 9), lost (a real last ball spent on a brick, then the loss screen
once the goal's fog has lifted: the goal flagged, the dotted run to the open cell, "N BRICKS AWAY") and instructions (level 1's
card with its glint line). The pixels are read back from the running SDL app.
"""
from pathlib import Path
import argparse
import os
import subprocess
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/figures/glint'
# (file, level, scene, frames before the picture)
SHOTS=[('1-glint-level4','4','glint',40),('2-glint-level9','9','glint',40),
       ('3-loss-level4','4','lost',150),('4-loss-level9','9','lost',150),('5-card-level1','1','instructions',10)]


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--exe',default=str(ROOT/'build/windows/TapDemo.exe'))
    args=parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True,exist_ok=True)
    exe=Path(args.exe)
    for name,level,scene,frames in SHOTS:
        bmp=OUT/(name+'.bmp')
        env=dict(os.environ,YY_TAPDEMO_LEVEL=level,YY_TAPDEMO_SCENE=scene,YY_SCREENSHOT_PATH=str(bmp))
        p=subprocess.run([str(exe),'--smoke',str(frames)],cwd=exe.parent,env=env,capture_output=True,text=True,timeout=60,creationflags=0x08000000)
        if p.returncode or not bmp.exists(): raise RuntimeError(f'{name}: exit {p.returncode}: {p.stdout} {p.stderr}')
        image=Image.open(bmp).convert('RGB')
        if image.size != (390,844): raise RuntimeError(f'{name}: expected 390x844, got {image.size}')
        image.save(bmp.with_suffix('.png')); bmp.unlink()
        print(f'{name}: level {level} {scene} captured, {image.size[0]}x{image.size[1]}',flush=True)


if __name__=='__main__': main()
