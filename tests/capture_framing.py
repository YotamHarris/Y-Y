"""T24 opening-frame captures at phone size (390x844). Run through fe_manager.py gpu, after scripts/build.ps1.

Each level opens on the field (the staged `field` scene: instructions dismissed, nothing touched) and is read back from the
running SDL app. `--tag before` or `--tag after` names the files docs/figures/framing/<tag>-level-N.png.
"""
from pathlib import Path
import argparse
import os
import subprocess
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/figures/framing'


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--tag',required=True); parser.add_argument('--levels',default='1,6,10')
    parser.add_argument('--exe',default=str(ROOT/'build/windows/TapDemo.exe'))
    args=parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True,exist_ok=True)
    exe=Path(args.exe)
    for level in args.levels.split(','):
        bmp=OUT/f'{args.tag}-level-{level}.bmp'
        env=dict(os.environ,YY_TAPDEMO_LEVEL=level,YY_TAPDEMO_SCENE='field',YY_SCREENSHOT_PATH=str(bmp))
        p=subprocess.run([str(exe),'--smoke','30'],cwd=exe.parent,env=env,capture_output=True,text=True,timeout=60,creationflags=0x08000000)
        if p.returncode or not bmp.exists(): raise RuntimeError(f'level {level}: exit {p.returncode}: {p.stdout} {p.stderr}')
        image=Image.open(bmp).convert('RGB')
        if image.size != (390,844): raise RuntimeError(f'level {level}: expected 390x844, got {image.size}')
        image.save(bmp.with_suffix('.png')); bmp.unlink()
        print(f'level {level}: {args.tag} captured, {image.size[0]}x{image.size[1]}',flush=True)


if __name__=='__main__': main()
