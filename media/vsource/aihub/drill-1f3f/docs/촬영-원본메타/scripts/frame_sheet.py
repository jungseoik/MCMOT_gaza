#!/usr/bin/env python3
"""슬레이트 판독용 프레임 시트 생성 — 각 프레임 좌상단에 1..N 번호를 찍는다.

ffmpeg 에 libfreetype 이 없어 drawtext 를 못 쓰므로 Pillow 로 직접 합성한다.
모든 카메라를 fps=30 으로 뽑아 60프레임 = 2.0초 창을 만든다. 60fps 카메라도
최종 산출물이 30fps 라 30fps 격자로 읽으면 기준이 통일된다.

  python3 frame_sheet.py <concat.mp4> <힌트초> <출력.jpg> [열 행 타일폭 fps]

fps 를 카메라 네이티브 값(60 등)으로 주면 그 카메라의 모든 프레임을 빠짐없이 본다.
30fps 격자로 60fps 카메라를 읽으면 접촉 순간이 건너뛴 프레임에 있을 때 판독이
최대 1/60초 늦어진다 — 오디오 교차검증에서 소리가 영상보다 빨라지는(불가능한)
값으로 드러난다.
"""
import subprocess, sys
from PIL import Image, ImageDraw, ImageFont

src, hint, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
COLS = int(sys.argv[4]) if len(sys.argv) > 4 else 10
ROWS = int(sys.argv[5]) if len(sys.argv) > 5 else 6
TW   = int(sys.argv[6]) if len(sys.argv) > 6 else 640
TH   = TW * 9 // 16
N    = COLS * ROWS
FPS  = int(sys.argv[7]) if len(sys.argv) > 7 else 30
start = hint - (N / 2) / FPS          # 힌트가 정확히 가운데(N/2+1 번)에 오도록

raw = subprocess.run(
    ['ffmpeg', '-v', 'error', '-nostdin', '-ss', f'{start:.4f}', '-i', src,
     '-vf', f'fps={FPS},scale={TW}:{TH}', '-frames:v', str(N),
     '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
    capture_output=True).stdout

got = len(raw) // (TW * TH * 3)
PAD, MARGIN = 4, 4
sheet = Image.new('RGB', (COLS*TW + (COLS+1)*PAD, ROWS*TH + (ROWS+1)*PAD), 'white')
font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', TH//7)

for i in range(got):
    fr = Image.frombytes('RGB', (TW, TH), raw[i*TW*TH*3:(i+1)*TW*TH*3])
    d = ImageDraw.Draw(fr)
    label = str(i+1)
    l, t, r, b = d.textbbox((0, 0), label, font=font)
    bw, bh = r-l+MARGIN*3, b-t+MARGIN*3
    d.rectangle([0, 0, bw, bh], fill=(0, 0, 0))          # 번호 배경 (가독성)
    d.text((MARGIN*1.5-l, MARGIN*1.5-t), label, font=font, fill=(255, 230, 0))
    x, y = i % COLS, i // COLS
    sheet.paste(fr, (PAD + x*(TW+PAD), PAD + y*(TH+PAD)))

sheet.save(out, quality=88)
print(f"{out}  {got}프레임  1번={start:.4f}초  힌트({hint:.1f}초)={N//2+1}번  1프레임=1/{FPS}초")
