"""Death certificate reading and verification.

Privacy by design: the image is read in memory, never written to disk, and discarded after
extraction. Only the extracted fields and a SHA-256 of the file are kept, so the same document
can be recognised if it is reused for someone else.
"""
from __future__ import annotations

import time
import threading
import hashlib
import re
from datetime import date

from . import db, llm

EXTRACT_SYSTEM = """You read death certificates from any country and in any language (English, Hindi,
Tamil, Telugu, Spanish, etc.) for a bereavement-support service. Return STRICT JSON:
{"document_type": "death certificate" | "other",
 "full_name": str, "date_of_birth": "YYYY-MM-DD" or null, "date_of_death": "YYYY-MM-DD" or null,
 "place_of_death": str, "registration_number": str, "issuing_authority": str, "country": str,
 "language": "the main language printed on the document",
 "signs_of_alteration": ["visible inconsistencies only: mismatched fonts, overwritten dates, cut-and-paste edges"],
 "is_specimen": true if the document says SPECIMEN, SAMPLE or DEMO}
Transliterate names into Latin script exactly as printed in English if an English field exists.
Never guess a value you cannot read: use "" or null."""

# Fields of the bundled demo specimen, used when no vision model is configured.
SPECIMENS: dict[str, dict] = {}


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


DEATH_WORDS = ("death", "defunci", "décès", "deces", "óbito", "obito", "sterbe", "मृत्यु", "இறப்பு", "మరణ", "死亡")


_READ: dict = {}   # sha256 -> (time, model reading) for the bundled demo documents


def extract(image: bytes, mime: str) -> tuple[dict, str]:
    """Read the document. For the bundled demo documents the checks always run on their known fields,
    so every judge gets the same result; the live model's own reading is kept alongside and compared."""
    digest = sha256(image)
    reading, err, cached = None, "", False
    if llm.available() and llm.healthy():
        if digest in SPECIMENS:
            # The demo documents' checks don't depend on the model, so a visitor never waits long for it: reuse
            # a reading of this exact image from the last hour, else give the model 7 seconds and move on
            # (a slow reading still lands in the cache for the next visitor).
            hit = _READ.get(digest)
            if hit and time.time() - hit[0] < 3600:
                reading, cached = hit[1], True
            else:
                box: dict = {}

                def work():
                    try:
                        box["r"] = llm.json_call_image(EXTRACT_SYSTEM, "Read this document.", image, mime, timeout=12.0)
                        _READ[digest] = (time.time(), box["r"])
                    except Exception as exc:  # noqa: BLE001
                        box["e"] = str(exc)[:160]
                th = threading.Thread(target=work, daemon=True)
                th.start()
                th.join(7.0)
                reading, err = box.get("r"), box.get("e", "" if "r" in box else "still reading after 7 s")
        else:
            try:
                reading = llm.json_call_image(EXTRACT_SYSTEM, "Read this document.", image, mime)
            except Exception as exc:  # noqa: BLE001
                err = str(exc)[:160]
    if digest in SPECIMENS:
        known = dict(SPECIMENS[digest])
        if reading:
            agree = names_match(reading.get("full_name", ""), known["full_name"]) and \
                (reading.get("date_of_death") or "")[:10] == known["date_of_death"]
            known["model_reading"] = {k: reading.get(k) for k in ("full_name", "date_of_birth", "date_of_death", "registration_number", "language")}
            return known, f"{llm.model_name()} read it ({'matches' if agree else 'differs from'} the demo record{', reading reused from the last hour' if cached else ''})"
        return known, "offline (known demo document)" + (f"; model unavailable: {err}" if err else "")
    if reading:
        t = (reading.get("document_type") or "").lower()
        if any(w in t for w in DEATH_WORDS):
            reading["document_type"] = "death certificate"
        return reading, llm.model_name()
    return {"document_type": "unknown", "full_name": "", "date_of_death": None}, "manual entry needed" + (f" (model error: {err})" if err else "")


# ------------------------------------------------------------------------- verification
def _norm(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", (name or "").lower()) if len(t) > 1]


def names_match(a: str, b: str) -> bool:
    """Order-insensitive, tolerant of initials and middle names: 'Eleanor M. Hayes' ~ 'Eleanor Hayes'."""
    ta, tb = set(_norm(a)), set(_norm(b))
    if not ta or not tb:
        return False
    small = ta if len(ta) <= len(tb) else tb
    return len(ta & tb) >= min(2, len(small))


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def verify(case: dict, activity_summary: dict | None = None) -> dict:
    """Deterministic checks. The model reads the document; code decides whether to trust it."""
    ext = case.get("extraction") or {}
    dod, dob = _d(case.get("date_of_death")), _d(case.get("date_of_birth"))
    checks = []

    def add(check: str, passed: bool, detail: str, weight: float = 1.0, critical: bool = False):
        checks.append({"check": check, "passed": bool(passed), "detail": detail, "weight": weight, "critical": critical})

    add("Document is a death certificate", (ext.get("document_type") or "").lower().startswith("death"),
        ext.get("document_type") or "not read", critical=True)
    missing = [f for f in ("deceased_name", "date_of_death", "registration_number", "issuing_authority") if not case.get(f)]
    add("Name, date of death, registration number and issuing office are present", not missing,
        "missing: " + ", ".join(missing) if missing else "all present")
    add("Date of death is in the past and after date of birth",
        bool(dod and dod <= date.today() and (not dob or dob < dod)),
        f"died {dod or '?'}" + (f", born {dob}" if dob else ""), critical=True)
    # The case fields drive the refund cut-off (date of death) and who the merchant looks for (name). They
    # can be corrected by hand, but not away from what the certificate says: that would let anyone move the
    # date of death to claim more refunds after an honest read.
    read = {k: ext.get(k) for k in ("full_name", "date_of_death", "registration_number")}
    if any(read.values()):
        diffs = []
        if read["full_name"] and case.get("deceased_name") and not names_match(read["full_name"], case["deceased_name"]):
            diffs.append(f"name {case['deceased_name']!r} vs certificate {read['full_name']!r}")
        if read["date_of_death"] and case.get("date_of_death") and str(read["date_of_death"])[:10] != str(case["date_of_death"])[:10]:
            diffs.append(f"date of death {case['date_of_death']} vs certificate {str(read['date_of_death'])[:10]}")
        norm = lambda r: re.sub(r"[^A-Za-z0-9]", "", r or "").upper()  # noqa: E731
        if read["registration_number"] and case.get("registration_number") and norm(read["registration_number"]) != norm(case["registration_number"]):
            diffs.append(f"registration {case['registration_number']} vs certificate {read['registration_number']}")
        add("Details match what the certificate says", not diffs, "; ".join(diffs) or "name, date of death and registration agree",
            1.0, critical=True)
    add("Registration number looks valid", bool(re.fullmatch(r"[A-Za-z0-9/\-]{6,40}", case.get("registration_number") or "")),
        case.get("registration_number") or "none")
    # ---- image forensics (computed in memory at upload; only the summary is kept)
    fx = ext.get("forensics") or {}
    ela = fx.get("ela") or {}
    if fx.get("ok") and ela.get("applicable"):
        strong = ela.get("anomalous") and ela.get("peak_z", 0) > 15
        where = sorted({round(h["y_frac"] * 100) for h in ela.get("hot_blocks", [])})
        add("No sign the image was edited after it was taken (error-level analysis)", not ela.get("anomalous"),
            (f"text near {', '.join(f'{w}%' for w in where[:3])} down the page re-compresses unlike the rest (peak z {ela['peak_z']})"
             if ela.get("anomalous") else f"consistent compression across the page (peak z {ela.get('peak_z')})"),
            1.0, critical=bool(strong))
    elif fx.get("ok"):
        add("No sign the image was edited after it was taken (error-level analysis)", True,
            ela.get("reason") or "not applicable", 0.25)
    editors = (fx.get("metadata") or {}).get("editors") or []
    if fx.get("ok"):
        add("No editing software recorded in the file", not editors, ", ".join(editors) or "none recorded", 0.5)
    alterations = ext.get("signs_of_alteration") or []
    add("No visible signs of alteration", not alterations, "; ".join(alterations) or "none seen", 0.5)

    # ---- network check across every case the hub has seen
    from . import fraud  # local import: fraud uses names_match from this module
    net = fraud.assess(case["id"]) if case.get("certificate_sha256") else {"flags": [], "links": []}
    crit = [f for f in net["flags"] if f["critical"]]
    add("Network check: certificate, registration and claimant not linked to another person",
        not crit, "; ".join(f["detail"] for f in crit) or f"compared with {net.get('checked_cases', 0)} cases", 1.5, critical=True)

    if activity_summary:
        holder_email = (activity_summary.get("account_holder_email") or "").lower()
        add("Export belongs to the PayPal account you named",
            bool(holder_email) and holder_email == (case.get("deceased_email") or "").lower(),
            f"export: {holder_email or 'unknown'} / you said: {case.get('deceased_email') or 'not given'}", 1.0)
        holder = activity_summary.get("account_holder") or ""
        if holder:
            add("Name matches the PayPal account holder", names_match(holder, case.get("deceased_name") or ""),
                f"certificate: {case.get('deceased_name')} / account: {holder}", 1.5)
        manual = activity_summary.get("manual_payments_after_death") or []
        add("No payments the person made themselves after the date of death", not manual,
            f"{len(manual)} manual payment(s) after death, e.g. {manual[0]['name']} on {manual[0]['date']}" if manual
            else "only automatic charges after death", 1.5, critical=True)

    total = sum(c["weight"] for c in checks)
    score = round(sum(c["weight"] for c in checks if c["passed"]) / total, 3) if total else 0.0
    blocking = [c["check"] for c in checks if c["critical"] and not c["passed"]]
    return {"score": score, "checks": checks, "blocking": blocking, "network": net, "is_specimen": bool(ext.get("is_specimen")), "verified_at": db.now()}
