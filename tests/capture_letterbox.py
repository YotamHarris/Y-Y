"""T27 captures: a panned board corner, also during shake. Run through fe_manager.py gpu.

`--tag before` or `--tag after` names docs/figures/letterbox/<tag>-<WxH>.png; `--exe` picks the build (the "before" is the previous
rendering with the same fixture hooks). The 393x759 case represents a phone safe area's different aspect ratio.
edge-touch reaches the corner through pinch and pan handlers; edge-shake adds a frozen maximum shake.
--verify checks that every pixel outside the logical frame (apart from a pixel of rounding) is the backdrop.
"""
from pathlib import Path
import argparse
import os
import math
import subprocess
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/figures/letterbox'


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--tag',required=True); parser.add_argument('--sizes',default='393x759,390x844,600x844,390x1000,800x600')
    parser.add_argument('--exe',default=str(ROOT/'build/windows/TapDemo.exe')); parser.add_argument('--scene',default='edge')
    parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True,exist_ok=True)
    exe=Path(args.exe)
    for size in args.sizes.split(','):
        bmp=OUT/f'{args.tag}-{size}.bmp'
        if bmp.exists(): bmp.unlink()
        env=dict(os.environ,YY_TAPDEMO_LEVEL='6',YY_TAPDEMO_SCENE=args.scene,YY_WINDOW_SIZE=size,YY_SCREENSHOT_PATH=str(bmp))
        p=subprocess.run([str(exe),'--smoke','30'],cwd=exe.parent,env=env,capture_output=True,text=True,timeout=60,creationflags=0x08000000)
        if p.returncode or not bmp.exists(): raise RuntimeError(f'{size}: exit {p.returncode}: {p.stdout} {p.stderr}')
        image=Image.open(bmp).convert('RGB')
        width,height=map(int,size.split('x'))
        if image.size!=(width,height): raise AssertionError(f'{size}: unexpected capture size {image.size}')
        image.save(bmp.with_suffix('.png')); bmp.unlink()
        if args.verify:
            scale=min(width/390,height/844)
            left=(width-390*scale)/2; top=(height-844*scale)/2
            right=left+390*scale; bottom=top+844*scale
            strips=[(0,0,max(0,math.floor(left)-1),height),(min(width,math.ceil(right)+1),0,width,height),
                    (0,0,width,max(0,math.floor(top)-1)),(0,min(height,math.ceil(bottom)+1),width,height)]
            for box in strips:
                if box[0]>=box[2] or box[1]>=box[3]: continue
                colors=image.crop(box).getcolors(maxcolors=width*height)
                if not colors or any(color!=(9,20,33) for _,color in colors):
                    raise AssertionError(f'{size}: field or effects spilled into strip {box}')
        print(f'{size}: {args.tag} captured, {image.size[0]}x{image.size[1]}',flush=True)


if __name__=='__main__': main()
