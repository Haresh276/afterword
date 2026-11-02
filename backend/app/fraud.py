"""The network check: signals no single merchant can see on its own.

Every case is a node. Cases are joined (union-find) when they share a certificate (exact SHA-256
or a perceptual-hash match), a registration number, the same person (name + date of birth), or
the same claimant. Inside the connected component of a new case we look for patterns that mean
fraud, and for honest duplicates (two siblings reporting the same parent) that should be linked
rather than notified twice.

Real-world motivation: in 2026 one fabricated death was used for four funeral claims at four
insurers within a week; it was caught only because a shared platform could see all four.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import db, forensics
from .certificate import names_match

VELOCITY_WINDOW_DAYS = 30
VELOCITY_LIMIT = 3


class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _norm_reg(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _person(c: dict) -> str:
    toks = sorted(t for t in re.findall(r"[a-z]+", (c.get("deceased_name") or "").lower()) if len(t) > 1)
    return " ".join(toks) + "|" + (c.get("date_of_birth") or "")


def assess(case_id: str) -> dict:
    cases = db.query("SELECT id, claimant_email, claimant_name, deceased_name, date_of_birth, registration_number, "
                     "certificate_sha256, certificate_phash, status, created_at FROM cases WHERE certificate_sha256 IS NOT NULL")
    me = next((c for c in cases if c["id"] == case_id), None)
    if not me:
        return {"component": [], "flags": [], "links": []}
    uf = _UF()
    keys: dict[str, list[str]] = defaultdict(list)
    for c in cases:
        uf.find(c["id"])
        for k in (f"sha:{c['certificate_sha256']}", f"reg:{_norm_reg(c['registration_number'])}" if c["registration_number"] else None,
                  f"person:{_person(c)}" if c["deceased_name"] else None, f"claimant:{(c['claimant_email'] or '').lower()}"):
            if k:
                keys[k].append(c["id"])
    for ids in keys.values():
        for i in ids[1:]:
            uf.union(ids[0], i)
    for i, a in enumerate(cases):                       # perceptual-hash near duplicates (pairwise is fine at hub scale per day)
        for b in cases[i + 1:]:
            if a["certificate_phash"] and b["certificate_phash"] and \
                    forensics.hamming(a["certificate_phash"], b["certificate_phash"]) <= forensics.PHASH_MATCH:
                uf.union(a["id"], b["id"])

    root = uf.find(case_id)
    comp = [c for c in cases if uf.find(c["id"]) == root and c["id"] != case_id]
    flags, links = [], []
    for o in comp:
        same_person = names_match(o["deceased_name"] or "", me["deceased_name"] or "") and \
            (not o["date_of_birth"] or not me["date_of_birth"] or o["date_of_birth"] == me["date_of_birth"])
        same_doc = o["certificate_sha256"] == me["certificate_sha256"] or (
            o["certificate_phash"] and me["certificate_phash"] and
            forensics.hamming(o["certificate_phash"], me["certificate_phash"]) <= forensics.PHASH_MATCH)
        same_reg = _norm_reg(o["registration_number"]) and _norm_reg(o["registration_number"]) == _norm_reg(me["registration_number"])
        if same_doc and not same_person:
            flags.append({"kind": "certificate_reused", "critical": True, "case": o["id"],
                          "detail": f"the same certificate image was used for {o['deceased_name']} in another case"})
        if same_reg and not same_person:
            flags.append({"kind": "registration_reused", "critical": True, "case": o["id"],
                          "detail": f"registration {me['registration_number']} belongs to {o['deceased_name']}"})
        if same_person and o["claimant_email"].lower() != (me["claimant_email"] or "").lower():
            links.append({"case": o["id"], "claimant": o["claimant_name"], "status": o["status"], "created_at": o["created_at"],
                          "detail": f"{o['claimant_name']} already reported this death"})

    since = (datetime.now(timezone.utc) - timedelta(days=VELOCITY_WINDOW_DAYS)).isoformat()
    mine = [c for c in cases if (c["claimant_email"] or "").lower() == (me["claimant_email"] or "").lower() and c["created_at"] >= since]
    people = {_person(c) for c in mine if c["deceased_name"]}
    if len(people) >= VELOCITY_LIMIT:
        flags.append({"kind": "velocity", "critical": True, "case": None,
                      "detail": f"this claimant reported {len(people)} different deaths in {VELOCITY_WINDOW_DAYS} days"})
    return {"component": [c["id"] for c in comp], "flags": flags, "links": links, "checked_cases": len(cases)}
