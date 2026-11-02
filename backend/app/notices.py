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


def _key() -> Ed25519PrivateKey:
    pem = db.kv_get("hub_private_key")
    if not pem:
        k = Ed25519PrivateKey.generate()
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
        db.kv_set("hub_private_key", pem)
    return serialization.load_pem_private_key(pem.encode(), password=None)


def public_key_b64() -> str:
    raw = _key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(body: dict) -> str:
    return base64.b64encode(_key().sign(canonical(body))).decode()


def verify(body: dict, signature: str, public_key: str | None = None) -> bool:
    try:
        pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key or public_key_b64()))
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
        "requested": ["cancel_recurring", "refund_charges_after_death", "cancel_open_invoices"],
    }
