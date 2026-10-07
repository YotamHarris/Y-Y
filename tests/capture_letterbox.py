"""T27 captures: the board's bottom-right edge, close up, in a window that is not 390x844. Run through fe_manager.py gpu.

`--tag before` or `--tag after` names docs/figures/letterbox/<tag>-<WxH>.png; `--exe` picks the build (the "before" is the previous
commit's build). Wider and taller windows letterbox the 390x844 frame, so the field's edge and the strips beside it show.
"""
from pathlib import Path
import argparse
import os
import subprocess
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/figures/letterbox'


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--tag',required=True); parser.add_argument('--sizes',default='390x844,600x844,390x1000,800x600')
    parser.add_argument('--exe',default=str(ROOT/'build/windows/TapDemo.exe')); parser.add_argument('--scene',default='edge')
    args=parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True,exist_ok=True)
    exe=Path(args.exe)
    for size in args.sizes.split(','):
        bmp=OUT/f'{args.tag}-{size}.bmp'
        env=dict(os.environ,YY_TAPDEMO_LEVEL='6',YY_TAPDEMO_SCENE=args.scene,YY_WINDOW_SIZE=size,YY_SCREENSHOT_PATH=str(bmp))
        p=subprocess.run([str(exe),'--smoke','30'],cwd=exe.parent,env=env,capture_output=True,text=True,timeout=60,creationflags=0x08000000)
        if p.returncode or not bmp.exists(): raise RuntimeError(f'{size}: exit {p.returncode}: {p.stdout} {p.stderr}')
        image=Image.open(bmp).convert('RGB')
        image.save(bmp.with_suffix('.png')); bmp.unlink()
        print(f'{size}: {args.tag} captured, {image.size[0]}x{image.size[1]}',flush=True)


if __name__=='__main__': main()
