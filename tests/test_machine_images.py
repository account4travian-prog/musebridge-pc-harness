import io
import pytest
from PIL import Image,ImageFont,ImageDraw

from musebridge.machine_images import prepare,MAX_BYTES
from musebridge.media import from_bytes


def test_four_k_noise_capture_is_resized_marked_and_within_transport(tmp_path):
    source=tmp_path/'four-k.png'
    Image.effect_noise((3840,2160),100).convert('RGB').save(source)
    target=tmp_path/'prepared.jpg'
    result=prepare(source,target,[{'ref':'e12','rectangle':{'left':100,'top':100}}])
    assert result['original_size']==[3840,2160]
    assert max(result['size_pixels'])==1568 and result['size']<=MAX_BYTES
    assert result['marks']==['e12']
    assert from_bytes(target.read_bytes(),'image/jpeg').mime_type=='image/jpeg'


def test_crop_coordinates_preserve_source_and_explicit_attachments_are_unmodified(tmp_path):
    source=tmp_path/'source.png'
    Image.new('RGB',(300,200),'red').save(source)
    original=source.read_bytes()
    result=prepare(source,tmp_path/'crop.jpg',[],{'x':10,'y':20,'width':100,'height':80})
    assert result['size_pixels']==[100,80] and source.read_bytes()==original
    attachment=from_bytes(original,'image/png')
    assert attachment.data==original
    with pytest.raises(ValueError):prepare(source,tmp_path/'bad.jpg',[],{'x':290,'y':0,'width':30,'height':20})


def test_windows_ocr_reads_synthetic_text_without_a_model(tmp_path):
    from musebridge.windows_ocr import recognize
    source=tmp_path/'ocr.png'
    image=Image.new('RGB',(600,150),'white')
    ImageDraw.Draw(image).text((20,25),'MuseBridge 593271',fill='black',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',44))
    image.save(source)
    result=recognize(source)
    assert result['engine']=='Windows.Media.Ocr'
    assert any('593271' in line['text'] for line in result['lines'])
