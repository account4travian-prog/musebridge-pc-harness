"""Bounded Windows OCR perception worker; no model or reasoning loop."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys


def recognize(path):
    """Recognize an owned screenshot in a separate, time-bounded process."""
    result=subprocess.run([sys.executable,'-m','musebridge.windows_ocr',str(path)],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or len(result.stdout)>20000:raise OSError('Windows OCR is unavailable')
    return json.loads(result.stdout)


async def _read(path):
    from PIL import Image
    from winrt.windows.graphics.imaging import SoftwareBitmap,BitmapPixelFormat,BitmapAlphaMode
    from winrt.windows.storage.streams import DataWriter
    from winrt.windows.media.ocr import OcrEngine
    with Image.open(path) as opened:
        if opened.width*opened.height>16_000_000:raise ValueError('OCR image too large')
        original=opened.size
        image=opened.convert('RGBA')
        image.thumbnail((1568,1568))
        writer=DataWriter()
        writer.write_bytes(image.tobytes('raw','BGRA'))
        bitmap=SoftwareBitmap(BitmapPixelFormat.BGRA8,image.width,image.height,BitmapAlphaMode.IGNORE)
        bitmap.copy_from_buffer(writer.detach_buffer())
        engine=OcrEngine.try_create_from_user_profile_languages()
        if engine is None:raise OSError('No installed OCR language')
        result=await engine.recognize_async(bitmap)
        scale=original[0]/image.width
        lines=[]
        for index,line in enumerate(result.lines):
            if index>=100:break
            boxes=[word.bounding_rect for word in line.words]
            if not boxes:continue
            left=min(box.x for box in boxes)*scale
            top=min(box.y for box in boxes)*scale
            right=max(box.x+box.width for box in boxes)*scale
            bottom=max(box.y+box.height for box in boxes)*scale
            lines.append({'ref':'t'+str(index+1),'text':line.text[:200],
                          'rectangle':{'left':round(left),'top':round(top),'right':round(right),'bottom':round(bottom)}})
        bitmap.close()
        writer.close()
        return {'lines':lines,'engine':'Windows.Media.Ocr','coordinate_space':'window pixels'}


if __name__=='__main__':
    try:
        from .document_worker import limit_windows_process_memory
        limit_windows_process_memory()
        data=asyncio.run(_read(Path(sys.argv[1])))
        output=json.dumps(data).encode()
        if len(output)>20000:raise ValueError('OCR output too large')
        sys.stdout.buffer.write(output)
    except BaseException:
        raise SystemExit(3)
