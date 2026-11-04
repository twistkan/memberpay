"""Generate the original geometric MemberPay mark; no external font assets."""
from pathlib import Path
from PIL import Image, ImageDraw
image=Image.new('RGBA',(256,256),(0,0,0,0))
d=ImageDraw.Draw(image)
d.rounded_rectangle((8,8,248,248),radius=48,fill='#102a43')
d.line([(48,184),(48,76),(90,124),(132,76),(132,184)],fill='white',width=15,joint='curve')
d.line([(164,184),(164,76),(206,76),(218,90),(218,118),(206,132),(164,132)],fill='#5dd6bd',width=15,joint='curve')
image.save(Path(__file__).parent/'app_icon.ico',sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
