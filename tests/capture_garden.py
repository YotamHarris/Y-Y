"""T16 named screenshots. Run through fe_manager.py gpu, after scripts/build.ps1 -Smoke.

Capture pixels from the running SDL app, convert losslessly to PNG, and place a
390-wide readback beside its accepted concept. Never substitutes device evidence.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import shutil
import hashlib
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/figures/garden-pop'
CASES=[
    ('gameplay','10','aim','garden-gameplay.png',False),
    ('damage','10','garden-damage','garden-damage.png',True),
    ('mid-hit','10','garden-hit','garden-damage.png',True),
    ('powers-goal','10','glow','garden-powers.png',True),
    ('instructions','10','instructions','garden-instructions.png',False),
    ('fit','10','palette-fit','garden-gameplay.png',True),
    ('max','10','palette-max','garden-powers.png',True),
    ('new-game','1','instructions','garden-instructions.png',False),
    ('debug','10','debug','garden-plate.jpg',False),
]


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--case',action='append'); args=parser.parse_args()
    if not os.environ.get('FE_MANAGER_GPU'):
        parser.error('launch this capture through fe_manager.py gpu')
    OUT.mkdir(parents=True,exist_ok=True)
    raw=ROOT/'build/garden-captures'; raw.mkdir(parents=True,exist_ok=True)
    # POST_BUILD copies assets after a link. An artwork-only iteration need not relink;
    # synchronize the candidate's assets explicitly before recording its runtime pixels.
    shutil.copytree(ROOT/'games/tapdemo/assets',ROOT/'build/windows/assets/tapdemo',dirs_exist_ok=True)
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'games/tapdemo/assets/garden').glob('*.bmp')}
    records=[]
    for name,level,scene,concept,fixture in CASES:
        if args.case and name not in args.case: continue
        bmp=raw/(name+'.bmp')
        env=dict(os.environ,YY_TAPDEMO_LEVEL=level,YY_TAPDEMO_SCENE=scene,YY_SCREENSHOT_PATH=str(bmp))
        env.pop('YY_TAPDEMO_SCHEME',None)
        p=subprocess.run([str(ROOT/'build/windows/TapDemo.exe'),'--smoke','45'],cwd=ROOT/'build/windows',
                         env=env,capture_output=True,text=True,timeout=30,creationflags=0x08000000)
        if p.returncode or not bmp.exists(): raise RuntimeError(f'{name}: exit {p.returncode}: {p.stdout} {p.stderr}')
        actual=Image.open(bmp).convert('RGB'); actual.save(OUT/(name+'.png'))
        actual=actual.resize((390,round(actual.height*390/actual.width)),Image.Resampling.LANCZOS)
        reference=Image.open(ROOT/'docs/figures/reskin-concepts'/concept).convert('RGB')
        reference=reference.resize((390,round(reference.height*390/reference.width)),Image.Resampling.LANCZOS)
        compare=Image.new('RGB',(810,max(actual.height,reference.height)+32),(246,244,234))
        draw=ImageDraw.Draw(compare)
        draw.text((10,8),f'T16 runtime: {name}',fill=(32,65,40))
        draw.text((410,8),'Accepted Garden Pop concept',fill=(32,65,40))
        compare.paste(actual,(10,28)); compare.paste(reference,(410,28))
        compare.save(OUT/(name+'-compare.png'))
        records.append({'name':name,'scene':scene,'level':level,'debug_fixture':fixture,
                        'capture':name+'.png','readback':name+'-compare.png','concept':concept,
                        'artwork_sha256':hashes,
                        'command':'TapDemo.exe --smoke 45','read_width':390,'capture_size':Image.open(bmp).size})
        print(f'{name}: captured and paired at 390 wide',flush=True)
    manifest=OUT/'captures.json'
    if args.case and manifest.exists():
        old=json.loads(manifest.read_text()); names={r['name'] for r in records}
        records=[r for r in old if r['name'] not in names]+records
    manifest.write_text(json.dumps(records,indent=2)+'\n')


if __name__=='__main__': main()
