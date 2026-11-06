"""Builds the final video: trimmed capture + voice-over lines at their scenes + captions + end card.

    python scripts/video/compose.py <out_dir> [--no-voice]   -> <out_dir>/afterword_demo.mp4 (1920x1080)
"""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1])
VOICE = "--no-voice" not in sys.argv
plan = json.loads((OUT / "durations.json").read_text())
cues = json.loads((OUT / "cues.json").read_text())
raw = Path((OUT / "raw_path.txt").read_text().strip())
DUR, LINES = plan["durations"], {l["id"]: l["text"] for l in plan["lines"]}
start = {c["id"]: c["t"] for c in cues["cues"]}
offset, body_len = cues["video_offset"], start["_end"] + 0.8
W, H, END = 1920, 1080, 4.0
INTER = "/usr/share/fonts/opentype/inter/"
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
INK, PAPER, MOSS, MUTED = (29, 38, 38), (238, 241, 239), (47, 93, 82), (85, 97, 95)


def run(args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def wrap(d, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + [cur]


def caption(path, text):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(INTER + "Inter-Medium.otf", 36)
    lines = wrap(d, text, f, 1560)
    h = 54 * len(lines) + 40
    y0 = H - h - 44
    d.rounded_rectangle((W // 2 - 830, y0, W // 2 + 830, y0 + h), radius=16, fill=(29, 38, 38, 225))
    for i, ln in enumerate(lines):
        d.text((W // 2, y0 + 20 + 27 + 54 * i), ln, font=f, fill=(255, 255, 255), anchor="mm")
    im.save(path)


def end_card(path):
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    d.arc((W // 2 - 70, 240, W // 2 + 70, 460), 200, 340, fill=MOSS, width=12)
    d.ellipse((W // 2 - 14, 252, W // 2 + 14, 280), fill=MOSS)
    d.text((W // 2, 560), "Afterword", font=ImageFont.truetype(SERIF, 116), fill=INK, anchor="mm")
    d.text((W // 2, 680), "Tell us once.", font=ImageFont.truetype(INTER + "Inter-Regular.otf", 48), fill=MUTED, anchor="mm")
    f3 = ImageFont.truetype(INTER + "Inter-Regular.otf", 30)
    d.text((W // 2, 900), "github.com/Haresh276/afterword", font=f3, fill=MOSS, anchor="mm")
    d.text((W // 2, 960), "PayPal Agent Toolkit · Gemini · AG Studio · Bryntum · Render · all people and companies are fictional",
           font=ImageFont.truetype(INTER + "Inter-Regular.otf", 24), fill=(139, 149, 147), anchor="mm")
    im.save(path)


# 1. trimmed, scaled, captioned body
ids = [i for i in LINES if i != "end" and i in start]
for i in ids:
    caption(OUT / f"cap_{i}.png", LINES[i])
inputs = ["-ss", f"{offset:.2f}", "-t", f"{body_len:.2f}", "-i", str(raw)]
for i in ids:
    inputs += ["-i", str(OUT / f"cap_{i}.png")]
fc = "[0:v]scale=1728:1080,pad=1920:1080:96:0:color=0xeef1ef[v0];"
for n, i in enumerate(ids):
    a, b = start[i] + 0.25, start[i] + 0.25 + DUR[i] + 0.35
    fc += f"[v{n}][{n + 1}:v]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})'[v{n + 1}];"
run([*inputs, "-filter_complex", fc.rstrip(";"), "-map", f"[v{len(ids)}]", "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
     "-crf", "20", "-pix_fmt", "yuv420p", str(OUT / "body.mp4")])
end_card(OUT / "end.png")
run(["-loop", "1", "-t", str(END), "-i", str(OUT / "end.png"), "-vf", "fade=t=in:st=0:d=0.5", "-r", "30", "-c:v", "libx264",
     "-preset", "veryfast", "-pix_fmt", "yuv420p", str(OUT / "end.mp4")])
(OUT / "list.txt").write_text(f"file '{OUT / 'body.mp4'}'\nfile '{OUT / 'end.mp4'}'\n")
run(["-f", "concat", "-safe", "0", "-i", str(OUT / "list.txt"), "-c", "copy", str(OUT / "silent.mp4")])
total = body_len + END

# 2. voice-over: every line at its scene start, the last one over the end card
final = OUT / ("afterword_demo.mp4" if VOICE else "afterword_demo_captions_only.mp4")
if VOICE:
    ain, mix = [], ""
    order = ids + ["end"]
    for n, i in enumerate(order):
        at = (start[i] + 0.25) if i != "end" else body_len + 0.6
        ain += ["-i", str(OUT / f"vo_{i}.wav")]
        mix += f"[{n + 1}:a]adelay={int(at * 1000)}|{int(at * 1000)},volume=1.0[a{n}];"
    mix += "".join(f"[a{n}]" for n in range(len(order))) + f"amix=inputs={len(order)}:normalize=0,apad,atrim=0:{total:.2f},loudnorm=I=-16:TP=-1.5[aout]"
    run(["-i", str(OUT / "silent.mp4"), *ain, "-filter_complex", mix, "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "160k", "-shortest", str(final)])
else:
    run(["-i", str(OUT / "silent.mp4"), "-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=48000:cl=stereo", "-c:v", "copy",
         "-c:a", "aac", "-shortest", str(final)])
d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)]).decode())
print(f"{final}  {d:.1f}s")

# 3. timed script for a human voice-over (same timings)
rows = ["Afterword: voice-over script. Each line starts at the time shown; read it at a calm pace.", ""]
for i in ids + ["end"]:
    at = (start[i] + 0.25) if i != "end" else body_len + 0.6
    rows.append(f"{int(at // 60)}:{at % 60:04.1f}  ({DUR[i]:.0f}s)  {LINES[i]}")
(OUT / "voiceover_script.txt").write_text("\n".join(rows) + "\n")
