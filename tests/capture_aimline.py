"""T25 aim-line and last-ball captures at phone size (390x844). Run through fe_manager.py gpu, after scripts/build.ps1.

Each is a staged scene (YY_TAPDEMO_SCENE) with a real held aim: aim-brick (the line ends on the brick above the pocket), aim-wall
(the column above is emptied, so it ends on the wall), lastball (the brick scene with one ball left: the LAST BALL banner) and
aim-far (zoomed in, the wall far above: the camera zooms out so the line's end stays on screen; run with --frames 60 so the ease settles).
`--only aim-far --tag before` captures just that scene as docs/figures/aimline/before-aim-far.png.
The pixels are read back from the running SDL app.
"""
from pathlib import Path
import argparse
import os
import subprocess
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/figures/aimline'
SHOTS=[('1-brick','aim-brick'),('2-wall','aim-wall'),('3-last-ball','lastball'),('4-far','aim-far')]


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--level',default='1'); parser.add_argument('--only'); parser.add_argument('--tag',default=''); parser.add_argument('--frames',default='10'); parser.add_argument('--exe',default=str(ROOT/'build/windows/TapDemo.exe'))
    args=parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True,exist_ok=True)
    exe=Path(args.exe)
    for name,scene in SHOTS:
        if args.only and scene!=args.only: continue
        name=(args.tag+'-' if args.tag else '')+name
        bmp=OUT/(name+'.bmp')
        env=dict(os.environ,YY_TAPDEMO_LEVEL=args.level,YY_TAPDEMO_SCENE=scene,YY_SCREENSHOT_PATH=str(bmp))
        p=subprocess.run([str(exe),'--smoke',args.frames],cwd=exe.parent,env=env,capture_output=True,text=True,timeout=60,creationflags=0x08000000)
        if p.returncode or not bmp.exists(): raise RuntimeError(f'{name}: exit {p.returncode}: {p.stdout} {p.stderr}')
        image=Image.open(bmp).convert('RGB')
        if image.size != (390,844): raise RuntimeError(f'{name}: expected 390x844, got {image.size}')
        image.save(bmp.with_suffix('.png')); bmp.unlink()
        print(f'{name}: captured, {image.size[0]}x{image.size[1]}',flush=True)


if __name__=='__main__': main()
