"""Build the original MuseBridge launch icon; no external artwork required."""
from pathlib import Path
from PIL import Image, ImageDraw


def main():
    image = Image.new('RGBA',(256,256),(0,0,0,0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8,8,248,248),radius=54,fill='#5179e8')
    draw.line([(65,186),(65,72),(128,146),(191,72),(191,186)],fill='white',width=20,joint='curve')
    target = Path(__file__).resolve().parents[1] / 'musebridge' / 'assets' / 'musebridge.ico'
    target.parent.mkdir(exist_ok=True)
    image.save(target,format='ICO',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


if __name__ == '__main__':main()
