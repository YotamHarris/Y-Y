"""Package the approved imagegen sheet as phone-resolution straight-alpha BMPs.

Requires Pillow only to rebuild. No generated artwork is drawn by this script: it
crops, scales and packs it. Texture ranges enforce the approved fog contrast rule;
the two UI panels are assembled offline with nine-slice to keep their painted rims.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'docs/figures/garden-pop/source-atlas.png'
DEST = ROOT / 'games/tapdemo/assets/garden'
spec = importlib.util.spec_from_file_location('assets', ROOT / 'scripts/generate-assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)
NAMES = ['grass', 'cut', 'soil', 'clear', 'holder', 'mist', 'ball', 'flag',
         'flash', 'burst', 'clippings', 'header', 'card', 'rim-h', 'rim-v', 'rim-corner']


def bmp(path, image):
    image = image.convert('RGBA')
    values = list(image.getdata())
    assets.bmp32(path, *image.size, lambda x, y: values[y * image.width + x])


def texture(image, low, high):
    # Mirror-repeat a generated interior, so no painted outside edge repeats as a grid.
    w, h = image.size
    interior = image.crop((int(w*.22), int(h*.22), int(w*.78), int(h*.78)))
    gray = ImageOps.grayscale(interior).resize((128, 128), Image.Resampling.LANCZOS)
    lo, hi = gray.getextrema()
    pixels = [tuple(round(a+(b-a)*(v-lo)/max(1,hi-lo)) for a,b in zip(low,high))+(255,) for v in gray.getdata()]
    quarter = Image.new('RGBA', (128,128)); quarter.putdata(pixels)
    out = Image.new('RGBA', (256,256))
    out.paste(quarter,(0,0)); out.paste(ImageOps.mirror(quarter),(128,0))
    out.paste(ImageOps.flip(quarter),(0,128)); out.paste(ImageOps.flip(ImageOps.mirror(quarter)),(128,128))
    return out


def panel(image, size):
    # All corners and strokes are original generated pixels; stretch only the middles.
    w,h=image.size; border=max(8,int(min(w,h)*.20))
    xs=[0,border,w-border,w]; ys=[0,border,h-border,h]
    edge=36; dx=[0,edge,size[0]-edge,size[0]]; dy=[0,edge,size[1]-edge,size[1]]
    out=Image.new('RGBA',size)
    for row in range(3):
        for col in range(3):
            part=image.crop((xs[col],ys[row],xs[col+1],ys[row+1]))
            part=part.resize((dx[col+1]-dx[col],dy[row+1]-dy[row]),Image.Resampling.LANCZOS)
            out.paste(part,(dx[col],dy[row]))
    return out


def luminance(rgb):
    channels=[v/255/12.92 if v/255<=.04045 else ((v/255+.055)/1.055)**2.4 for v in rgb[:3]]
    return sum(a*b for a,b in zip(channels,(.2126,.7152,.0722)))


def main():
    source=Image.open(SOURCE).convert('RGBA'); cells=[]; boxes=[]
    for i in range(16):
        c,r=i%4,i//4
        box=(round(c*source.width/4),round(r*source.height/4),round((c+1)*source.width/4),round((r+1)*source.height/4))
        cell=source.crop(box)
        # The generated sheet crosses two nominal row boundaries by a few pixels.
        # Exclude the adjacent soil/card/strip before tightening each actual cutout.
        top=12 if i in (4,5,6) else 0
        bottom=round(cell.height*.94) if i in (8,10) else cell.height
        region=cell.crop((0,top,cell.width,bottom))
        bound=region.getchannel('A').point(lambda a: 255 if a>16 else 0).getbbox()
        bound=(bound[0],bound[1]+top,bound[2],bound[3]+top)
        cells.append(cell.crop(bound)); boxes.append([box,bound])
    sheet=Image.new('RGBA',(1040,1040))
    for i,cell in enumerate(cells):
        if i==3: cell=texture(cell,(18,34,24),(37,57,34))
        elif i==5: cell=texture(cell,(154,177,143),(215,231,205))
        elif i>=13: cell=texture(cell,(220,236,202),(250,255,235))
        else: cell=ImageOps.contain(cell,(256,256),Image.Resampling.LANCZOS)
        slot=Image.new('RGBA',(256,256)); slot.paste(cell,((256-cell.width)//2,(256-cell.height)//2))
        sheet.paste(slot,((i%4)*260+2,(i//4)*260+2))
    DEST.mkdir(parents=True,exist_ok=True)
    bmp(DEST/'tiles.bmp',sheet)
    bmp(DEST/'header.bmp',panel(cells[11],(1146,216)))
    bmp(DEST/'card.bmp',panel(cells[12],(1098,2232)))
    extrema={}
    for name,i,fn in [('darkest_mist',5,min),('brightest_cavity',3,max)]:
        pixels=list(sheet.crop((i%4*260+2,i//4*260+2,i%4*260+258,i//4*260+258)).getdata())
        rgb=fn(pixels,key=luminance); extrema[name]={'rgb':rgb[:3],'luminance':luminance(rgb)}
    extrema['luminance_gap']=extrema['darkest_mist']['luminance']-extrema['brightest_cavity']['luminance']
    record={'created':'2026-10-06','author':'Codex with built-in imagegen; original production artwork',
            'task':'T16 Garden Pop','reference':'docs/figures/reskin-concepts/garden-atlas.png',
            'source':str(SOURCE.relative_to(ROOT)).replace('\\','/'),'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'prompt':'docs/figures/garden-pop/prompt.txt','source_size':source.size,'source_crops':dict(zip(NAMES,boxes)),
            'sheet':{'file':'tiles.bmp','cell_size':256,'stride':260,'padding':2,'reading_order':NAMES},
            'panels':{'header.bmp':[1146,216],'card.bmp':[1098,2232]},
            'processing':'Crop generated alpha, Lanczos phone scaling; mirror-repeat texture interiors in conservative approved luminance ranges; offline nine-slice UI. Original icon silhouettes are drawn over the generated holder. Straight-alpha BITMAPV4HEADER, no runtime shaders.',
            'fog_contrast':extrema,'font':'Fredoka SemiBold, assets/fonts/OFL.txt'}
    (DEST/'sources.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(extrema))


if __name__=='__main__': main()
