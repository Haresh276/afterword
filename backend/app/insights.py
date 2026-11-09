"""Rows for the AG Studio network report (For businesses -> Insights).

Two sources, always labelled in a `source` column so nobody mistakes one for the other:
  * Live       - read from this hub's database (the cases you ran in the demo, lab attacks, blocks)
  * Simulated  - a deterministic 90-day history across the seven demo businesses, generated with
                 production rules (72-hour objection window, auto-close caps, a human review queue),
                 so the charts have shape on a fresh install. Seeded, so it's the same every time.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from . import db, seed

STATUS = {"CLOSED": "Closed", "PAUSED": "Paused, in window", "IN_REVIEW": "Needs review", "ACTIONED": "Partly done",
          "RECEIVED": "Received", "SENT": "Arriving", "WITHDRAWN": "Withdrawn (alive)", "REJECTED": "Rejected"}
STEP = {"suspend_subscription": "Pause billing", "activate_subscription": "Resume billing", "cancel_subscription": "Cancel subscription",
        "refund_capture": "Refund a charge", "cancel_sent_invoice": "Cancel invoice"}
RELATIONSHIPS = [("daughter", 26), ("son", 24), ("spouse", 22), ("executor", 10), ("sibling", 8), ("grandchild", 6), ("friend", 4)]
LANGS = [("English", 52), ("Hindi", 18), ("Tamil", 12), ("Telugu", 10), ("Other", 8)]
SAFETY_KINDS = [("Edited certificate", "Forensics (error-level analysis)", 9), ("Certificate reused for another person", "Fraud network", 5),
                ("Registration number reused", "Fraud network", 3), ("Many deaths from one claimant", "Fraud network", 2),
                ("Account holder answered: alive", "Proof-of-life window", 4), ("Payment made after the date of death", "Activity check", 6),
                ("Notice failed signature check", "Ed25519 signature", 1)]


def _pick(rng: random.Random, weighted):
    return rng.choices([w[0] for w in weighted], weights=[w[-1] for w in weighted])[0]


def _simulated(today: date) -> dict:
    rng = random.Random(20261112)
    notices, actions, safety = [], [], []
    merchants = {m[0]: m for m in seed.MERCHANTS}
    for day in range(90, 0, -1):
        d = today - timedelta(days=day)
        growth = 1 + (90 - day) / 45                      # the network grows over the quarter
        for _ in range(rng.choices([0, 1, 2, 3, 4], weights=[3, 5, 4, 2, 1])[0] + int(growth > 2.2)):
            mid = rng.choice(list(merchants))
            _, name, cat, amount, auto, cap, _, kind = merchants[mid]
            nid = f"sim_n{len(notices):04d}"
            after_death = rng.choices([0, 1, 2, 3], weights=[3, 5, 2, 1])[0] if kind != "invoice" else 0
            refund = round(after_death * amount, 2)
            human = (not auto) or refund > cap or rng.random() < 0.08
            secs_to_pause = round(rng.uniform(2, 25), 1)   # every business pauses on arrival, even when a person reviews the rest
            hours = 72 + (rng.uniform(0.02, 0.2) if not human else rng.uniform(1, 30))
            closed_on = datetime.combine(d, datetime.min.time()) + timedelta(hours=hours)
            withdrawn = rng.random() < 0.012
            status = "Withdrawn (alive)" if withdrawn else ("Closed" if closed_on.date() <= today else
                                                           ("Needs review" if human and rng.random() < 0.4 else "Paused, in window"))
            notices.append({"id": nid, "source": "Simulated", "merchant": name, "category": cat, "received": d.isoformat(),
                            "status": status, "closedBy": ("Person" if human else "Auto-close rule") if status == "Closed" else "Not closed yet",
                            "secondsToPause": secs_to_pause if kind != "invoice" else None,
                            "hoursToClose": round(hours, 1) if status == "Closed" else None,
                            "refunded": refund if status == "Closed" else 0.0,
                            "yearlyStopped": round(amount * (1 if kind == "annual" else 12 if kind == "sub" else 0), 2) if status == "Closed" else 0.0,
                            "notices": 1, "relationship": _pick(rng, RELATIONSHIPS), "language": _pick(rng, LANGS)})
            steps = ([("suspend_subscription", 0.0), ("cancel_subscription", 0.0)] if kind != "invoice" else [("cancel_sent_invoice", 0.0)])
            steps += [("refund_capture", amount)] * after_death
            for tool, amt in steps:
                done = status == "Closed" or (tool == "suspend_subscription" and status != "Needs review")
                actions.append({"id": f"sim_a{len(actions):05d}", "source": "Simulated", "merchant": name, "noticeId": nid,
                                "step": STEP[tool], "reversible": "Reversible" if tool == "suspend_subscription" else "Irreversible",
                                "status": "Rejected (withdrawn)" if withdrawn and tool != "suspend_subscription" else ("Done" if done else "Waiting"),
                                "approvedBy": ("Person" if human else "Auto-close rule") if done else "-",
                                "amount": amt, "date": d.isoformat(), "actions": 1,
                                "irreversibleDone": int(done and tool != "suspend_subscription" and not withdrawn)})
            if withdrawn:
                actions.append({"id": f"sim_a{len(actions):05d}", "source": "Simulated", "merchant": name, "noticeId": nid, "step": "Resume billing",
                                "reversible": "Reversible", "status": "Done", "approvedBy": "Objection policy", "amount": 0.0,
                                "date": d.isoformat(), "actions": 1, "irreversibleDone": 0})
        if rng.random() < 0.33:
            kind, layer, _ = rng.choices(SAFETY_KINDS, weights=[k[2] for k in SAFETY_KINDS])[0]
            safety.append({"id": f"sim_s{len(safety):04d}", "source": "Simulated", "date": d.isoformat(), "attempt": kind,
                           "stoppedBy": layer, "outcome": "Blocked before anyone was told", "events": 1})
    return {"notices": notices, "actions": actions, "safety": safety}


def _live() -> dict:
    notices, actions, safety = [], [], []
    for n in db.query("""SELECT n.*, m.name AS merchant, m.category, c.relationship, c.language FROM notices n
                         JOIN merchants m ON m.id=n.merchant_id LEFT JOIN cases c ON c.id=n.case_id"""):
        acts = db.query("SELECT * FROM pending_actions WHERE notice_id=?", (n["id"],))
        pause = next((a for a in acts if a["tool"] == "suspend_subscription" and a["status"] == "EXECUTED"), None)
        t0 = datetime.fromisoformat(n["received_at"]) if n["received_at"] else None
        secs = round((datetime.fromisoformat(pause["decided_at"]) - t0).total_seconds(), 1) if pause and t0 and pause["decided_at"] else None
        hours = round((datetime.fromisoformat(n["closed_at"]) - t0).total_seconds() / 3600, 3) if n["closed_at"] and t0 else None
        by = sorted({a["decided_by"] or "" for a in acts if a["status"] == "EXECUTED"})
        out = n["outcome"] or {}
        f = db.query("SELECT yearly_cost FROM findings WHERE notice_id=?", (n["id"],))
        notices.append({"id": n["id"], "source": "Live", "merchant": n["merchant"], "category": n["category"],
                        "received": (n["received_at"] or n["created_at"])[:10], "status": STATUS.get(n["status"], n["status"]),
                        "closedBy": ("Auto-close rule" if all(b.startswith("policy") for b in by) else "Person") if n["status"] == "CLOSED" else "Not closed yet",
                        "secondsToPause": secs, "hoursToClose": hours, "refunded": out.get("refunded_total", 0.0) or 0.0,
                        "yearlyStopped": (f[0]["yearly_cost"] or 0.0) if f and n["status"] == "CLOSED" else 0.0, "notices": 1,
                        "relationship": n["relationship"] or "-", "language": {"en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu"}.get(n["language"] or "", "Other")})
        for a in acts:
            actions.append({"id": a["id"], "source": "Live", "merchant": n["merchant"], "noticeId": n["id"], "step": STEP.get(a["tool"], a["tool"]),
                            "reversible": "Reversible" if a["tool"] in ("suspend_subscription", "activate_subscription") else "Irreversible",
                            "status": {"EXECUTED": "Done", "PENDING": "Waiting", "REJECTED": "Rejected (withdrawn)"}.get(a["status"], a["status"].title()),
                            "approvedBy": ("Auto-close rule" if (a["decided_by"] or "").startswith("policy:") and "objection" not in (a["decided_by"] or "")
                                           else "Objection policy" if "objection" in (a["decided_by"] or "") else "Person" if a["decided_by"] else "-"),
                            "amount": a["amount"] or 0.0, "date": a["created_at"][:10], "actions": 1,
                            "irreversibleDone": int(a["status"] == "EXECUTED" and a["tool"] not in ("suspend_subscription", "activate_subscription"))})
    for c in db.query("SELECT id, verification, network, objected_at, created_at, redteam FROM cases"):
        v = c["verification"] or {}
        for b in v.get("blocking") or []:
            layer = "Forensics (error-level analysis)" if "error-level" in b or "edited" in b else "Fraud network" if "Network" in b else "Activity check"
            safety.append({"id": f"{c['id']}:{len(safety)}", "source": "Live (lab)" if c["redteam"] else "Live", "date": c["created_at"][:10],
                           "attempt": b, "stoppedBy": layer, "outcome": "Blocked before anyone was told", "events": 1})
        if c["objected_at"]:
            safety.append({"id": f"{c['id']}:alive", "source": "Live", "date": c["objected_at"][:10], "attempt": "Account holder answered: alive",
                           "stoppedBy": "Proof-of-life window", "outcome": "Withdrawn, billing resumed", "events": 1})
    for r in db.query("SELECT id, ts, action, detail FROM audit_log WHERE actor='redteam_lab' AND action LIKE 'LAB %'"):
        safety.append({"id": f"lab{r['id']}", "source": "Live (lab)", "date": r["ts"][:10], "attempt": r["action"][4:].split(":")[0].replace("_", " "),
                       "stoppedBy": (r["detail"] or {}).get("blocked_by", "?"), "outcome": "Blocked" if "BLOCKED" in r["action"] and "NOT" not in r["action"] else "Not blocked",
                       "events": 1})
    return {"notices": notices, "actions": actions, "safety": safety}


def rows() -> dict:
    today = datetime.now(timezone.utc).date()
    sim, live = _simulated(today), _live()
    out = {k: live[k] + sim[k] for k in sim}
    out["asOf"] = today.isoformat()
    out["counts"] = {k: {"live": len(live[k]), "simulated": len(sim[k])} for k in sim}
    return out
