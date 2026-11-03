"""The Afterword notice: one signed message from a family's agent to a merchant's agent.

The hub signs each notice with Ed25519 after it has verified the certificate. Merchants verify
the signature with the hub's public key (published at /.well-known/afterword-hub.json), so a
notice can't be forged or edited in transit, and it carries only what a merchant needs:
name, email, date of death, the PayPal references, and the verification result.
"""
from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import db

PROTOCOL = "afterword/notice@1"


RECEIPT_PROTOCOL = "afterword/closure-receipt@1"


def _key(party: str = "hub") -> Ed25519PrivateKey:
    """Each party has its own Ed25519 key: the hub signs notices, each merchant signs its own receipts.
    (In this demo they live in one database; in production each merchant keeps its own key.)"""
    name = "hub_private_key" if party == "hub" else f"key:{party}"
    pem = db.kv_get(name)
    if not pem:
        k = Ed25519PrivateKey.generate()
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
        db.kv_set(name, pem)
    return serialization.load_pem_private_key(pem.encode(), password=None)


def public_key_b64(party: str = "hub") -> str:
    raw = _key(party).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(body: dict, party: str = "hub") -> str:
    return base64.b64encode(_key(party).sign(canonical(body))).decode()


def verify(body: dict, signature: str, public_key: str | None = None, party: str = "hub") -> bool:
    try:
        pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key or public_key_b64(party)))
        pk.verify(base64.b64decode(signature), canonical(body))
        return True
    except Exception:  # noqa: BLE001
        return False


def build(case: dict, finding: dict, merchant: dict, notice_id: str) -> dict:
    return {
        "protocol": PROTOCOL, "notice_id": notice_id, "issued_at": db.now(),
        "to_merchant": {"id": merchant["id"], "name": merchant["name"]},
        "deceased": {"name": case["deceased_name"], "paypal_email": case["deceased_email"],
                     "date_of_death": case["date_of_death"]},
        "references": finding["references_json"],
        "charges_after_death": finding["charges_after_death"],
        "verification": {"score": case["verification"]["score"],
                         "certificate_sha256": case["certificate_sha256"],
                         "issuing_authority": case.get("issuing_authority"),
                         "registration_number": case.get("registration_number"),
                         "checks_passed": sum(1 for c in case["verification"]["checks"] if c["passed"]),
                         "checks_total": len(case["verification"]["checks"])},
        "claimant": {"name": case["claimant_name"], "relationship": case["relationship"],
                     "contact": case["claimant_email"], "language": case.get("language") or "en"},
        "requested": ["pause_recurring_now", "cancel_recurring", "refund_charges_after_death", "cancel_open_invoices"],
        # Irreversible steps wait until this time. The account holder's own address has been told;
        # if they answer "I'm alive", the hub sends a signed withdrawal and paused billing is resumed.
        "objection_window": {"irreversible_not_before": case.get("objection_deadline"),
                             "subject_notified": bool(case.get("deceased_email"))},
        "money_flow": "Refunds go back to the original payment only. Nothing is ever paid to the person reporting.",
    }
