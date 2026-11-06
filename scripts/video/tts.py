"""Synthesises submission/narration.json with an offline Piper voice -> <out>/vo_<id>.wav + durations.json.

    python scripts/video/tts.py <voice.onnx> <out_dir> [--with-evidence]
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
voice, out = sys.argv[1], Path(sys.argv[2])
with_evidence = "--with-evidence" in sys.argv
out.mkdir(parents=True, exist_ok=True)
lines = [l for l in json.loads((ROOT / "submission" / "narration.json").read_text()) if with_evidence or not l.get("needs_evidence")]
durs = {}
for l in lines:
    wav = out / f"vo_{l['id']}.wav"
    subprocess.run([sys.executable, "-m", "piper", "-m", voice, "-f", str(wav), "--length-scale", "1.04", "--sentence-silence", "0.25"],
                   input=l["text"].encode(), check=True, capture_output=True)
    d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)]).decode())
    durs[l["id"]] = round(d, 2)
    print(f"{l['id']:9} {d:5.1f}s  {l['text'][:70]}")
(out / "durations.json").write_text(json.dumps({"lines": lines, "durations": durs}, indent=1))
print(f"total speech {sum(durs.values()):.1f}s")
