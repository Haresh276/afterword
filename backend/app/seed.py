"""Demo world: six fictional merchants on the Afterword network, one that isn't, a clearly marked
specimen death certificate, and a PayPal activity export for a fictional account holder.

All people, companies and documents here are invented. The certificate is stamped SPECIMEN and
names a fictional registry so it can never pass as a real document.
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import certificate, db
from . import paypal_gateway as pp

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
SAMPLES.mkdir(exist_ok=True)
SPECIMEN_PATH = SAMPLES / "specimen_certificate.png"
SANDBOX_DATA = SAMPLES / "sandbox_demo.json"   # written by scripts/sandbox_setup.py

DECEASED = {"given": "Lakshmi", "surname": "Narayanan", "name": "Lakshmi Narayanan",
            "email": os.getenv("DEMO_DECEASED_EMAIL") or os.getenv("DEMO_CLIENT_EMAIL") or "lakshmi.narayanan@example.com",
            "dob": "1951-03-14"}
CLAIMANT = {"name": "Priya Narayanan", "email": "priya.narayanan@example.com", "relationship": "daughter"}
DAYS_SINCE_DEATH = 28

MERCHANTS = [
    # id, name, category, monthly, auto_close, cap, accent, kind
    ("m_streambox", "StreamBox", "Streaming", 15.99, 1, 50, "#3a5a78", "sub"),
    ("m_cloudnest", "Cloudnest Photos", "Photo storage", 2.99, 1, 20, "#4f6f52", "sub"),
    ("m_fitpulse", "FitPulse", "Fitness app", 9.99, 1, 30, "#7a5c3e", "sub"),
    ("m_dailyledger", "The Daily Ledger", "News", 12.00, 0, 0, "#5b4b6b", "sub"),
    ("m_bookshelf", "Bookshelf+", "Audiobooks", 14.95, 1, 40, "#6b4f3a", "sub"),
    ("m_harbor", "Harbor Language School", "Lessons", 120.00, 1, 0, "#2f5d62", "invoice"),
    ("m_safehome", "SafeHome Antivirus", "Software", 59.99, 1, 0, "#3e5a63", "annual"),
]
PRICE_RISE = {"m_streambox": 13.99}   # StreamBox raised its price two charges ago (tests price-drift tolerance)
OFF_NETWORK = ("GreenLeaf Seeds", "orders@greenleafseeds.example", 18.00)


def dod() -> date:
    return date.today() - timedelta(days=DAYS_SINCE_DEATH)


# ------------------------------------------------------------------------- specimen certificate
def specimen_fields() -> dict:
    return {"document_type": "death certificate", "full_name": DECEASED["name"], "date_of_birth": DECEASED["dob"],
            "date_of_death": dod().isoformat(), "place_of_death": "Chennai, Tamil Nadu",
            "registration_number": f"AWD-{dod().year}-004117", "issuing_authority": "Afterword Demo Registry (fictional)",
            "country": "India", "language": "Hindi and English", "signs_of_alteration": [], "is_specimen": True}


def make_specimen() -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    f = specimen_fields()
    W, H = 1100, 1400
    img = Image.new("RGB", (W, H), "#fbfaf6")
    d = ImageDraw.Draw(img)
    serif = "/usr/share/fonts/truetype/freefont/FreeSerif.ttf"
    serif_b = "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf"
    try:
        T = ImageFont.truetype(serif_b, 44); S = ImageFont.truetype(serif, 30); L = ImageFont.truetype(serif, 24)
        V = ImageFont.truetype(serif_b, 30); WM = ImageFont.truetype(serif_b, 120)
    except OSError:
        T = S = L = V = WM = ImageFont.load_default()
    d.rectangle([30, 30, W - 30, H - 30], outline="#3d4a4a", width=4)
    d.rectangle([44, 44, W - 44, H - 44], outline="#9aa5a3", width=1)
    d.text((W / 2, 110), "मृत्यु प्रमाण पत्र", font=T, fill="#1f2a2b", anchor="mm")
    d.text((W / 2, 170), "CERTIFICATE OF DEATH", font=T, fill="#1f2a2b", anchor="mm")
    d.text((W / 2, 225), f["issuing_authority"], font=S, fill="#4b5757", anchor="mm")
    rows = [("नाम / Name", f["full_name"]), ("जन्म तिथि / Date of birth", f["date_of_birth"]),
            ("मृत्यु तिथि / Date of death", f["date_of_death"]), ("मृत्यु स्थान / Place of death", f["place_of_death"]),
            ("पंजीकरण संख्या / Registration no.", f["registration_number"]),
            ("पंजीकरण तिथि / Date of registration", (dod() + timedelta(days=3)).isoformat())]
    y = 330
    for label, value in rows:
        d.text((110, y), label, font=L, fill="#5d6969")
        d.text((110, y + 34), value, font=V, fill="#1f2a2b")
        d.line([110, y + 80, W - 110, y + 80], fill="#d8dedc", width=1)
        y += 120
    d.text((110, y + 40), "This is a SPECIMEN made for the Afterword demo. It is not an official document,", font=L, fill="#3d4a4a")
    d.text((110, y + 72), "names a fictional registry and has no legal value.", font=L, fill="#3d4a4a")
    wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(wm).text((W / 2, H / 2), "SPECIMEN", font=WM, fill=(170, 40, 40, 60), anchor="mm")
    img = Image.alpha_composite(img.convert("RGBA"), wm.rotate(30, center=(W / 2, H / 2))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def make_forged() -> bytes:
    """Demo of abuse: someone photographs Lakshmi's certificate and edits the name to cancel and get
    refunds on another person's subscriptions. Saved as a phone photo (JPEG 75), edited, re-saved."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(io.BytesIO(make_specimen())).convert("RGB")
    b = io.BytesIO()
    img.save(b, "JPEG", quality=75)
    img = Image.open(io.BytesIO(b.getvalue())).convert("RGB")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf", 30)
    except OSError:
        f = ImageFont.load_default()
    d.rectangle([105, 360, 900, 402], fill="#fbfaf6")
    d.text((110, 364), "Meera Venkatesan", font=f, fill="#1f2a2b")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=95)
    data = out.getvalue()
    certificate.SPECIMENS[certificate.sha256(data)] = {**specimen_fields(), "full_name": "Meera Venkatesan",
                                                       "date_of_birth": "1958-07-02"}
    return data


def ensure_specimen() -> bytes:
    data = make_specimen()  # regenerated so dates stay relative to today
    SPECIMEN_PATH.write_bytes(data)
    certificate.SPECIMENS[certificate.sha256(data)] = specimen_fields()
    return data


# ------------------------------------------------------------------------- merchants + PayPal data
def _ts(d: date, hh: int = 9) -> str:
    return datetime(d.year, d.month, d.day, hh, 12, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_merchants() -> None:
    for mid, name, cat, _amt, auto, cap, accent, _k in MERCHANTS:
        if not db.get("merchants", mid):
            slug = name.lower().replace(" ", "").replace("+", "plus")
            db.insert("merchants", {"id": mid, "name": name, "category": cat, "support_email": f"care@{slug}.example",
                                    "paypal_email": f"billing@{slug}.example", "auto_close": auto,
                                    "auto_refund_cap": cap, "accent": accent})


def seed_demo() -> dict:
    """Create merchants, their PayPal records (mock or real sandbox) and the matching activity CSV."""
    ensure_specimen()
    seed_merchants()
    rows: list[dict] = []
    death = dod()
    email = DECEASED["email"]

    sandbox = json.loads(SANDBOX_DATA.read_text()) if pp.MODE == "sandbox" and SANDBOX_DATA.exists() else None

    for mid, name, _cat, amount, _auto, _cap, _acc, kind in MERCHANTS:
        m = db.get("merchants", mid)
        if kind == "annual":
            if sandbox and mid not in sandbox["subscriptions"]:
                continue
            if sandbox:
                s = sandbox["subscriptions"][mid]
                sid, charges = s["subscription_id"], [(date.fromisoformat(c["date"]), c["capture_id"], c["amount"]) for c in s["captures"]]
            else:
                sid = pp.transport().seed_subscription(f"{name} annual", DECEASED["given"], DECEASED["surname"], email, amount, "USD",
                                                       _ts(date.today() - timedelta(days=725)))
                days = [date.today() - timedelta(days=725), date.today() - timedelta(days=360)]   # renews in 5 days
                charges = [(d, pp.transport().seed_capture(sid, _ts(d), amount, "USD"), amount) for d in days]
            db.insert("merchant_customers", {"id": db.new_id("cus"), "merchant_id": mid, "customer_name": DECEASED["name"],
                                             "email": email, "paypal_subscription_id": sid, "plan_name": f"{name} annual",
                                             "amount": amount, "currency": "USD", "status": "ACTIVE"})
            for d, cid, amt in charges:
                rows.append({"Date": d.strftime("%m/%d/%Y"), "Name": name, "Type": "Subscription Payment", "Status": "Completed",
                             "Gross": f"-{amt:.2f}", "To Email Address": m["paypal_email"], "Transaction ID": cid,
                             "Subscription Number": sid, "Item Title": f"{name} annual plan"})
        elif kind == "sub":
            if sandbox:  # real sandbox subscription created by scripts/sandbox_setup.py
                s = sandbox["subscriptions"][mid]
                sid, charges = s["subscription_id"], [(date.fromisoformat(c["date"]), c["capture_id"], c["amount"]) for c in s["captures"]]
            else:
                sid = pp.transport().seed_subscription(f"{name} monthly", DECEASED["given"], DECEASED["surname"], email, amount, "USD",
                                                       _ts(death - timedelta(days=200)))
                anchor = (sum(map(ord, mid)) % 20) + 2
                days = [death - timedelta(days=90 - anchor), death - timedelta(days=60 - anchor), death - timedelta(days=30 - anchor),
                        death + timedelta(days=min(anchor, DAYS_SINCE_DEATH - 1))]
                old = PRICE_RISE.get(mid, amount)
                amts = [old, old, amount, amount]
                charges = [(d, pp.transport().seed_capture(sid, _ts(d), a, "USD"), a) for d, a in zip(days, amts)]
            db.insert("merchant_customers", {"id": db.new_id("cus"), "merchant_id": mid, "customer_name": DECEASED["name"],
                                             "email": email, "paypal_subscription_id": sid, "plan_name": f"{name} monthly",
                                             "amount": amount, "currency": "USD", "status": "ACTIVE"})
            for d, cid, amt in charges:
                rows.append({"Date": d.strftime("%m/%d/%Y"), "Name": name, "Type": "Subscription Payment", "Status": "Completed",
                             "Gross": f"-{amt:.2f}", "To Email Address": m["paypal_email"], "Transaction ID": cid,
                             "Subscription Number": sid, "Item Title": f"{name} monthly"})
        else:
            iid = sandbox["invoices"][mid] if sandbox else pp.transport().seed_invoice(email, amount, "USD", "Spanish lessons, block of 8")
            db.insert("merchant_invoices", {"id": db.new_id("inv"), "merchant_id": mid, "email": email, "paypal_invoice_id": iid,
                                            "description": "Spanish lessons, block of 8", "amount": amount, "currency": "USD", "status": "UNPAID"})
            rows.append({"Date": (death - timedelta(days=12)).strftime("%m/%d/%Y"), "Name": name, "Type": "Invoice Received",
                         "Status": "Unpaid", "Gross": f"-{amount:.2f}", "To Email Address": m["paypal_email"],
                         "Transaction ID": "", "Invoice Number": iid, "Item Title": "Spanish lessons, block of 8"})

    # a merchant that is not on the network: the family gets a ready-to-send email instead
    gname, gmail, gamt = OFF_NETWORK
    for k, offset in enumerate((-75, -45, -15, 15)):
        rows.append({"Date": (death + timedelta(days=offset)).strftime("%m/%d/%Y"), "Name": gname, "Type": "Preapproved Payment Bill User Payment",
                     "Status": "Completed", "Gross": f"-{gamt:.2f}", "To Email Address": gmail, "Transaction ID": f"GL{k}8823AA19C",
                     "Subscription Number": "B-7GL04419XK2210345", "Item Title": "Seed of the month"})
    # things that must NOT be touched
    rows.append({"Date": (death - timedelta(days=50)).strftime("%m/%d/%Y"), "Name": "Kalpana Silks", "Type": "Express Checkout Payment",
                 "Status": "Completed", "Gross": "-86.00", "To Email Address": "shop@kalpanasilks.example", "Transaction ID": "KS5521093AB",
                 "Item Title": "Cotton saree"})
    rows.append({"Date": (death - timedelta(days=20)).strftime("%m/%d/%Y"), "Name": "Arun Narayanan", "Type": "Mobile Payment",
                 "Status": "Completed", "Gross": "-40.00", "To Email Address": "arun.n@example.com", "Transaction ID": "AN7730012ZQ",
                 "Item Title": ""})
    rows.append({"Date": (death - timedelta(days=33)).strftime("%m/%d/%Y"), "Name": "Arun Narayanan", "Type": "Mobile Payment",
                 "Status": "Completed", "Gross": "60.00", "From Email Address": "arun.n@example.com", "To Email Address": email,
                 "Transaction ID": "AN6610045ZQ"})

    db.kv_set("sample_activity_csv", to_csv(rows, email))
    db.audit("system", "DEMO seeded", None, {"mode": pp.MODE, "merchants": len(MERCHANTS)})
    return {"ok": True, "date_of_death": death.isoformat()}


COLUMNS = ["Date", "Time", "TimeZone", "Name", "Type", "Status", "Currency", "Gross", "Fee", "Net",
           "From Email Address", "To Email Address", "Transaction ID", "Subscription Number", "Invoice Number", "Item Title"]


def to_csv(rows: list[dict], holder_email: str) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
    w.writeheader()
    for r in sorted(rows, key=lambda r: datetime.strptime(r["Date"], "%m/%d/%Y")):
        out = {c: "" for c in COLUMNS}
        out.update({"Time": "09:12:00", "TimeZone": "IST", "Currency": "USD", "Fee": "0.00"})
        out.update(r)
        out["Net"] = out["Gross"]
        if float(out["Gross"]) < 0:
            out["From Email Address"] = holder_email
        w.writerow(out)
    return buf.getvalue()
