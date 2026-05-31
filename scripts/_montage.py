"""기준 이미지 몽타주 — 정중앙에 십자/256px 박스를 그려 defect 중심성 검증."""
from pathlib import Path
from PIL import Image, ImageDraw
import random

paths = sorted(Path("기준").rglob("*.jpg"))
random.seed(7)
# 종류별로 골고루 6장(웨이퍼도 섞이게)
by_type = {}
for p in paths:
    t = p.stem.split("_")[-1]
    by_type.setdefault(t, []).append(p)
pick = []
for t, ps in by_type.items():
    pick += random.sample(ps, min(3, len(ps)))
pick = pick[:9]

cell = 330
cols, rows = 3, 3
canvas = Image.new("RGB", (cols * cell, rows * cell), "white")
for i, p in enumerate(pick):
    im = Image.open(p).convert("RGB").resize((cell, cell))
    d = ImageDraw.Draw(im)
    c = cell // 2
    box = int(cell * 0.256)  # 1000px 기준 256px 중앙박스
    d.rectangle([c - box // 2, c - box // 2, c + box // 2, c + box // 2],
                outline=(255, 0, 0), width=2)
    d.line([c, c - 14, c, c + 14], fill=(255, 0, 0), width=2)
    d.line([c - 14, c, c + 14, c], fill=(255, 0, 0), width=2)
    d.text((4, 4), p.stem.split("_")[-1], fill=(255, 0, 0))
    canvas.paste(im, ((i % cols) * cell, (i // cols) * cell))
out = Path("기준_중심성_몽타주.png")
canvas.save(out)
print("saved", out, "picked:", [p.stem.split('_')[-1] for p in pick])
