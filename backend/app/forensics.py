"""Image forensics for uploaded certificates, done in memory. Nothing here keeps the image.

1. Perceptual hash (pHash, 64-bit DCT hash). A SHA-256 changes completely if a forger
   re-saves, crops or edits one field. pHash barely moves, so the hub can recognise the
   *same document* reused for a different person (Hamming distance <= PHASH_MATCH bits).
2. Error Level Analysis (JPEG ghosts). Re-compress across a sweep of JPEG qualities and measure how much each
   text block changes. Regions pasted in or re-typed after the original compression often re-compress
   differently from the rest of the page. We keep only a coarse 12x16 grid of block scores
   (the text itself can't be recovered from it) and a single anomaly score.
3. Metadata. Editing-software tags in EXIF/PNG text (Photoshop, GIMP, Canva...).

ELA is a known-weak signal (screenshots, PNGs and heavy re-compression all confuse it), so it is
advisory and never blocks on its own. pHash reuse with a different name *does* block.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageOps

PHASH_MATCH = 10          # bits of 64; McKeown & Buchanan-style thresholds for "same image"
GRID = (16, 12)           # columns x rows for the stored ELA heat grid
EDITORS = ("photoshop", "gimp", "canva", "pixlr", "affinity", "paint.net", "snapseed", "picsart", "fotor", "illustrator")


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2 / n)
    m[0] /= np.sqrt(2)
    return m


_D32 = _dct_matrix(32)


def phash(img: Image.Image) -> str:
    g = ImageOps.grayscale(img).resize((32, 32), Image.Resampling.LANCZOS)
    a = np.asarray(g, dtype=np.float64)
    dct = _D32 @ a @ _D32.T
    low = dct[:8, :8].flatten()
    bits = low[1:] > np.median(low[1:])          # skip the DC term
    bits = np.concatenate([[low[0] > np.median(low)], bits])
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


_STD_LUMA = np.array([16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
                      14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
                      49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99], dtype=np.float64)


def jpeg_quality(img: Image.Image) -> int | None:
    """Estimate the libjpeg quality the file was saved with, from its luminance quantisation table."""
    q = getattr(img, "quantization", None)
    if not q:
        return None
    table = np.array(q.get(0, list(q.values())[0]), dtype=np.float64)
    scale = table.mean() / _STD_LUMA.mean() * 100
    est = (200 - scale) / 2 if scale <= 100 else 5000 / scale
    return int(min(100, max(1, round(est))))


def _block_means(a: np.ndarray, bs: int) -> np.ndarray:
    H, W = a.shape[0] // bs * bs, a.shape[1] // bs * bs
    return a[:H, :W].reshape(H // bs, bs, W // bs, bs).mean(axis=(1, 3))


def ela(img: Image.Image) -> dict:
    """JPEG-ghost error-level analysis (after Farid, 2009), on luminance.

    Re-save the page at a sweep of JPEG qualities. Text that has been through the photo's original
    compression settles at that quality ("leaves a ghost"); text typed or pasted in afterwards
    hasn't been through it and stands out against the other text blocks. Only text is compared
    with text, page borders are ignored, and scores are robust z-scores (median / MAD)."""
    if jpeg_quality(img) is None:
        return {"applicable": False, "reason": "lossless image: no compression history to analyse",
                "peak_z": 0.0, "hot_blocks": [], "grid": [], "anomalous": False, "quality": None}
    g = np.asarray(ImageOps.grayscale(img), dtype=np.float64)
    bs = 16
    texture = _block_means(np.abs(np.diff(g, axis=1, prepend=g[:, :1])), bs)
    R, C = texture.shape
    margin = np.zeros_like(texture, dtype=bool)
    margin[max(1, R // 25):-max(1, R // 25), max(1, C // 25):-max(1, C // 25)] = True
    ink = (texture > np.percentile(texture, 70)) & margin
    if int(ink.sum()) < 20:
        return {"applicable": False, "reason": "too little text to compare", "peak_z": 0.0, "hot_blocks": [],
                "grid": [], "anomalous": False, "quality": None}
    src = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8))
    best = (-1.0, None, None)
    for q in range(60, 99, 3):
        buf = io.BytesIO()
        src.save(buf, "JPEG", quality=q)
        err = _block_means((g - np.asarray(Image.open(io.BytesIO(buf.getvalue())), dtype=np.float64)) ** 2, bs)
        vals = err[ink]
        med = float(np.median(vals))
        mad = float(np.median(np.abs(vals - med))) + 1e-6
        z = np.where(ink, (err - med) / (1.4826 * mad), 0.0)
        if z.max() > best[0]:
            best = (float(z.max()), q, z)
    peak, q, z = best
    rows, cols = GRID[1], GRID[0]
    grid = np.zeros((rows, cols))
    for r in range(rows):
        for c in range(cols):
            blk = z[r * R // rows:(r + 1) * R // rows, c * C // cols:(c + 1) * C // cols]
            grid[r, c] = max(0.0, float(blk.max())) if blk.size else 0.0
    hot_mask = z > 6
    frac = float(hot_mask.sum()) / max(1, int(ink.sum()))
    hot = sorted(({"row": int(r * rows // R), "col": int(c * cols // C), "z": round(float(z[r, c]), 1),
                   "y_frac": round(r / R, 3)} for r, c in zip(*np.where(hot_mask))), key=lambda x: -x["z"])
    return {"applicable": True, "peak_z": round(peak, 1), "hot_blocks": hot[:12], "quality": q,
            "hot_fraction": round(frac, 3), "grid": np.round(grid / (grid.max() + 1e-9), 2).tolist(),
            "anomalous": bool(peak > 10 and 0 < frac < 0.1)}


def metadata(img: Image.Image) -> dict:
    found = []
    try:
        exif = img.getexif()
        for tag in (305, 315, 11):                       # Software, Artist, ProcessingSoftware
            v = exif.get(tag)
            if v:
                found.append(str(v))
    except Exception:  # noqa: BLE001
        pass
    for k, v in (img.info or {}).items():
        if isinstance(v, str) and k.lower() in ("software", "comment", "description", "xml:com.adobe.xmp"):
            found.append(v[:200])
    editors = sorted({e for e in EDITORS for f in found if e in f.lower()})
    return {"software": found[:3], "editors": editors}


def analyse(data: bytes) -> dict:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": "not an image (PDF certificates are read but not image-checked)"}
    return {"ok": True, "phash": phash(img), "ela": ela(img), "metadata": metadata(img),
            "format": img.format, "size": list(img.size)}
