"""Letters people actually read at the worst time of their life: short, plain, specific, kind.
A model writes them when one is configured; otherwise careful templates are used."""
from __future__ import annotations

from . import llm

LANGS = {"en": "English", "es": "Spanish", "zh": "Simplified Chinese", "vi": "Vietnamese", "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "fr": "French"}
OFFLINE = ("en", "es", "hi")  # languages with a hand-written template when no model key is set

STYLE = """Write for a bereaved family member. Plain words, no corporate phrases ("we apologize for any
inconvenience", "valued customer"), no exclamation marks, no emojis, no religious assumptions.
Lead with what has been done, give exact amounts and dates, say clearly that nothing else is needed
from them. Under 120 words. Return only the letter text."""


ES_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _day(iso: str | None, lang: str = "en") -> str:
    """US style for English ('October 7, 2026'), '7 de octubre de 2026' for Spanish, ISO-ish otherwise."""
    from datetime import date
    try:
        d = date.fromisoformat((iso or "")[:10])
    except ValueError:
        return iso or ""
    if lang == "es":
        return f"{d.day} de {ES_MONTHS[d.month - 1]} de {d.year}"
    if lang == "hi":
        return d.strftime("%d-%m-%Y")
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _money(amount: float, cur: str = "USD") -> str:
    return f"{cur} {amount:,.2f}" if cur != "USD" else f"${amount:,.2f}"


def merchant_reply(merchant: dict, notice: dict, outcome: dict) -> str:
    body = notice["payload"]
    first = (body["claimant"]["name"] or "").split(" ")[0] or "there"
    deceased = body["deceased"]["name"]
    facts = (f"Merchant: {merchant['name']}. Writing to {body['claimant']['name']} ({body['claimant']['relationship']} of {deceased}). "
             f"Cancelled subscriptions: {len(outcome['cancelled'])}. Refunded: {_money(outcome['refunded_total'])} "
             f"across {len(outcome['refunded'])} charges taken after {body['deceased']['date_of_death']}. "
             f"Open invoices cancelled: {len(outcome['invoices_cancelled'])}. "
             f"Marketing and billing emails to the deceased: stopped.")
    if llm.available():
        try:
            return llm.text_call(STYLE + f" Language: {LANGS.get(body['claimant'].get('language', 'en'), 'English')}.", facts)
        except Exception:  # noqa: BLE001
            pass
    parts = [f"Dear {first},", "",
             f"We're very sorry about {deceased}. We've closed everything on our side:"]
    if outcome["cancelled"]:
        parts.append(f"• the subscription is cancelled, so there will be no more charges")
    if outcome["refunded"]:
        parts.append(f"• {_money(outcome['refunded_total'])} taken after {body['deceased']['date_of_death']} is being refunded to the PayPal account".replace(body['deceased']['date_of_death'], _day(body['deceased']['date_of_death'])))
    if outcome["invoices_cancelled"]:
        k = len(outcome['invoices_cancelled'])
        parts.append(f"• {'the open invoice is' if k == 1 else f'{k} open invoices are'} cancelled; nothing is owed")
    if outcome.get("emails_stopped"):
        parts.append("• we won't send any more emails addressed to them")
    parts += ["", "You don't need to do anything else.", "", f"{merchant['name']}"]
    return "\n".join(parts)


ES_TEMPLATE = """Querida {first}:

Sentimos mucho la pérdida de {deceased}.

Avisamos a {n} empresas. {closed} ya lo cerraron todo: no se cobrará nada más, y se están devolviendo a la cuenta de PayPal {refund} cobrados después del {date}.
{merchants}{pending_line}
No tiene que llamar a nadie.

Afterword"""

HI_TEMPLATE = """{first} जी,

{deceased} जी के निधन पर हमें गहरा दुःख है।

उनके PayPal खाते से जुड़े {n} व्यापारियों को सूचना भेज दी गई है।
{closed} जगह सब कुछ बंद हो चुका है: कोई नया शुल्क नहीं कटेगा।
{date} के बाद लिए गए {refund} वापस किए जा रहे हैं।
{pending_line}
आपको किसी को फ़ोन करने की ज़रूरत नहीं है।

Afterword"""


def family_closure(case: dict, findings: list[dict]) -> str:
    lang = case.get("language") or "en"
    first = (case.get("claimant_name") or "").split(" ")[0] or "there"
    notified = [f for f in findings if f.get("notice_id")]
    closed = [f for f in notified if f["status"] == "CLOSED"]
    offline = [f for f in findings if f["status"] == "NOT_ON_NETWORK" and f["selected"]]
    refund = sum(f.get("refunded") or 0 for f in closed)
    facts = {
        "first": first, "deceased": case.get("deceased_name"), "n": len(notified), "closed": len(closed),
        "refund": _money(refund), "date": _day(case.get("date_of_death")),
        "merchants": [f"{f['merchant_name']}: " + ("open invoice cancelled, nothing owed" if f["kind"] == "INVOICE"
                      else "cancelled" + (f", {_money(f.get('refunded') or 0)} refunded" if f.get("refunded") else ""))
                      for f in closed],
        "still_open": [f["merchant_name"] for f in notified if f["status"] != "CLOSED"],
        "not_on_network": [f["merchant_name"] for f in offline],
    }
    if llm.available():
        try:
            return llm.text_call(
                STYLE + f" Write in {LANGS.get(lang, 'English')} (native script). This is the final summary for the family.",
                str(facts))
        except Exception:  # noqa: BLE001
            pass
    if lang == "es":
        facts["date"] = _day(case.get("date_of_death"), "es")
        ms = "".join(f"• {f['merchant_name']}: " + ("factura abierta cancelada, no se debe nada" if f["kind"] == "INVOICE"
                     else "cancelado" + (f", {_money(f.get('refunded') or 0)} reembolsados" if f.get("refunded") else "")) + "\n"
                     for f in closed)
        parts = []
        if facts["still_open"]:
            parts.append(f"{', '.join(facts['still_open'])} todavía lo están revisando; le avisaremos en cuanto respondan.")
        if offline:
            parts.append(f"{', '.join(facts['not_on_network'])} aún no está en Afterword. Le dejamos el correo escrito; solo tiene que enviarlo.")
        return ES_TEMPLATE.format(first=first, deceased=facts["deceased"], n=facts["n"], closed=facts["closed"], refund=facts["refund"],
                                  date=facts["date"], merchants=("\n" + ms if ms else ""), pending_line=("\n" + " ".join(parts) + "\n") if parts else "")
    if lang == "hi":
        facts["date"] = _day(case.get("date_of_death"), "hi")
        parts = []
        if facts["still_open"]:
            parts.append(f"{', '.join(facts['still_open'])} अभी जाँच कर रहे हैं; जवाब आते ही आपको बता देंगे।")
        if offline:
            parts.append(f"{', '.join(facts['not_on_network'])} के लिए ईमेल तैयार है; बस भेजना है।")
        pending_line = " ".join(parts) if parts else "सब कुछ पूरा हो गया है।"
        return HI_TEMPLATE.format(first=first, deceased=facts["deceased"], n=facts["n"], closed=facts["closed"],
                                  date=facts["date"], refund=facts["refund"], pending_line=pending_line)
    lines = [f"Dear {first},", "",
             f"We're so sorry about {facts['deceased']}.", "",
             f"We told {facts['n']} companies. {facts['closed']} of them have closed everything: no more charges will be taken, "
             f"and {facts['refund']} taken after {facts['date']} is being returned to the PayPal account."]
    if facts["merchants"]:
        lines += [""] + [f"• {m}" for m in facts["merchants"]]
    if facts["still_open"]:
        lines += ["", f"{', '.join(facts['still_open'])} {'is' if len(facts['still_open']) == 1 else 'are'} still checking; "
                      "we'll tell you as soon as they reply."]
    if offline:
        lines += ["", f"{', '.join(facts['not_on_network'])} {'is' if len(offline) == 1 else 'are'} not on Afterword yet. "
                      "We've written the email for you below; you only need to press send."]
    lines += ["", "You don't need to phone anyone.", "", "Afterword"]
    return "\n".join(lines)


def offline_email(case: dict, finding: dict) -> str:
    """For merchants not on the Afterword network: a ready-to-send email the family can forward."""
    return (f"Subject: Account holder has died: please cancel and refund\n\n"
            f"Hello {finding['merchant_name']},\n\n"
            f"{case['deceased_name']} ({case['deceased_email']}) died on {_day(case['date_of_death'])}. "
            f"Please cancel their account and any recurring PayPal payments"
            + (f" (subscription {', '.join(finding['references_json']['subscription_ids'])})" if finding['references_json']['subscription_ids'] else "")
            + (f", and refund the {len(finding['charges_after_death'])} charge(s) taken after that date "
               f"({_money(finding['owed_back'], finding['currency'])})" if finding['charges_after_death'] else "")
            + f".\n\nA death certificate (registration {case['registration_number']}, {case['issuing_authority']}) "
              f"has been verified by Afterword; certificate fingerprint sha256 {case['certificate_sha256'][:16]}…\n\n"
              f"Thank you,\n{case['claimant_name']} ({case['relationship']})\n{case['claimant_email']}")


def objection_email(case: dict, link: str, window_text: str) -> tuple[str, str]:
    """Sent to the deceased's own address. Written so a grieving family member reading it isn't alarmed."""
    name = case.get("deceased_name") or "the account holder"
    subject = f"Was {name}'s death reported by mistake?"
    body = (f"Someone has told Afterword that {name} has died, so that their subscriptions can be stopped.\n\n"
            f"If you are {name} and this is a mistake, open this link within {window_text} and nothing more will happen:\n"
            f"{link}\n\n"
            f"Billing has been paused, not cancelled. If you object, every company resumes it and no money moves. "
            f"The report was made by {case.get('claimant_name')} ({case.get('relationship') or 'family'}).\n\n"
            f"If you are looking after {name}'s affairs after their death, you don't need to do anything. "
            f"We're sorry for your loss.\n\n- Afterword")
    return subject, body
