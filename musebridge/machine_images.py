"""Resize, crop and label machine observations; explicit user attachments stay unchanged."""
from io import BytesIO
import math
from pathlib import Path

from PIL import Image,ImageDraw,ImageFont,ImageOps

MAX_BYTES=2*1024*1024
LONGEST_SIDE=1568


def prepare(path, destination, marks=(), region=None):
    """Produce a bounded JPEG with labels in original-image pixel coordinates."""
    with Image.open(path) as source:
        if source.width*source.height>40_000_000 or max(source.size)>20000:
            raise ValueError('Machine image exceeds safe decode dimensions')
        image=ImageOps.exif_transpose(source).convert('RGB')
    original=image.size
    offset_x=offset_y=0
    if region is not None:
        if not isinstance(region,dict) or set(region)!={'x','y','width','height'}:
            raise ValueError('region requires x, y, width and height in original image pixels')
        x,y,width,height=(region[key] for key in ('x','y','width','height'))
        if any(type(value) is not int for value in (x,y,width,height)) or min(x,y)<0 or min(width,height)<=0 or x+width>image.width or y+height>image.height:
            raise ValueError('Crop region must be inside the original image')
        image=image.crop((x,y,x+width,y+height))
        offset_x,offset_y=x,y
    cropped=image.size
    image.thumbnail((LONGEST_SIDE,LONGEST_SIDE),Image.Resampling.LANCZOS)
    scale=image.width/cropped[0]
    draw=ImageDraw.Draw(image)
    try:font=ImageFont.truetype('C:/Windows/Fonts/consolab.ttf',18)
    except OSError:font=ImageFont.load_default(size=18)
    drawn=[]
    for mark in list(marks)[:150]:
        ref=mark.get('ref')
        rect=mark.get('rectangle')
        if not isinstance(ref,str) or len(ref)>20 or not isinstance(rect,dict):continue
        left,top=rect.get('left'),rect.get('top')
        if type(left) not in (int,float) or type(top) not in (int,float) or not math.isfinite(left) or not math.isfinite(top):continue
        x,y=round((left-offset_x)*scale),round((top-offset_y)*scale)
        if not 0<=x<image.width or not 0<=y<image.height:continue
        box=draw.textbbox((0,0),ref,font=font)
        width,height=box[2]-box[0]+6,box[3]-box[1]+6
        x=min(x,max(0,image.width-width))
        y=min(y,max(0,image.height-height))
        draw.rectangle((x,y,x+width,y+height),fill='#facc15',outline='black',width=1)
        draw.text((x+3,y+1-box[1]),ref,font=font,fill='black')
        drawn.append(ref)
    encoded=None
    for quality in (85,75,60,45,30):
        stream=BytesIO()
        image.save(stream,format='JPEG',quality=quality,optimize=True)
        if len(stream.getvalue())<=MAX_BYTES:
            encoded=stream.getvalue()
            break
    if encoded is None:raise ValueError('Machine image could not fit the image transport limit')
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_bytes(encoded)
    return {'path':str(destination),'size':len(encoded),'format':'jpeg',
            'original_size':list(original),'size_pixels':list(image.size),
            'region':region,'marks':drawn,'marks_available':bool(drawn)}
