"""Hinglish negotiation agent.

Two separate calls, deliberately not merged into one:
  1. negotiate()  -- conducts a short Hinglish exchange with the customer
  2. extract()    -- pulls a strict-schema outcome out of that exchange

Merging these two steps was the first design that got tried and it kept
mislabeling politeness ("dekh lenge") as a commitment. Keeping them
separate, with an explicit acknowledged/committed/refused split, fixed it.

Runs in one of two modes:
  - LIVE:      if ANTHROPIC_API_KEY is set, calls the real Claude API
  - SIMULATOR: otherwise, uses a lightweight deterministic rule-based stand-in
               so the whole pipeline runs out of the box with no key required.
Both modes produce the same Promise schema, so benchmark.py doesn't care
which one is active.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
from datetime import datetime, timedelta
from typing import Optional

from models import FailedPayment, Promise, PromiseStatus

_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
_MODE = "live" if _API_KEY else "simulator"

NEGOTIATION_SYSTEM_PROMPT = """You are a polite, brief payment-recovery caller for an Indian
merchant. Speak in natural Hinglish (mixed Hindi/English, as urban Indians
actually speak). Your ONLY goal this turn is to get the customer to state
a SPECIFIC date and amount they can pay by. Do not offer discounts. Do not
threaten. If the customer is vague, ask one clarifying question. Keep your
turn under 3 sentences."""

EXTRACTION_SYSTEM_PROMPT = """You extract structured outcomes from a payment recovery
conversation transcript. Return ONLY valid JSON, no prose, matching this
schema exactly:
{"status": "acknowledged"|"committed"|"refused", "date": "YYYY-MM-DD"|null,
 "amount": number|null, "confidence": number between 0 and 1}
"committed" requires BOTH a specific date AND a specific positive amount to be
clearly stated by the customer. Politeness or vague acknowledgement
("we'll see", "dekh lenge", "I'll try") is "acknowledged", not "committed".
Explicit refusal or dispute is "refused"."""

# Persona to authentic customer replies in Hinglish
_PERSONA_REPLIES = {
    "willing_to_pay": "Haan bhaiya, {date} tak pura {amount} pay kar dunga, netbanking se karunga.",
    "salary_delayed": "Salary aane mein thoda delay hai. {date} ko debit karna, pura {amount} de dunga.",
    "changes_date": "Abhi thoda tight hai. {date} tak {amount} pakka transfer kar dunga.",
    "vague_customer": "Thoda mushkil hai abhi, dekh lenge kya kar sakte hain.",
    "refuses": "Abhi bilkul paise nahi hain, mujhe call mat karo bar bar.",
    "dispute": "Yeh charge maine kiya hi nahi, ye galat hai, main dispute karunga.",
    "stop_requested": "STOP karo yeh calls aur messages, mujhe bilkul contact mat karo.",
    "fraud": "Kaun bol raha hai? Yeh mera account nahi hai, wrong number.",
}


def _stable_seed(record_id: str) -> int:
    """Deterministic seed derived from record_id so simulator transcripts are 100% reproducible."""
    import hashlib
    return int(hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:8], 16)


def negotiate(payment: FailedPayment, seed: Optional[int] = None) -> str:
    """Returns a transcript of the negotiation turn (customer's reply)."""
    if _MODE == "live":
        return _negotiate_live(payment)
    return _simulate_customer_turn(payment, seed)


def _simulate_customer_turn(payment: FailedPayment, seed: Optional[int] = None) -> str:
    rng = random.Random(seed if seed is not None else _stable_seed(payment.id))
    persona = getattr(payment, "customer_persona", "vague_customer")
    template = _PERSONA_REPLIES.get(persona, "Thoda mushkil hai, dekh lenge.")

    base_date = datetime(2026, 9, 3)
    offset = 3 if persona == "willing_to_pay" else (7 if persona == "salary_delayed" else 5)
    date_str = (base_date + timedelta(days=offset)).date().isoformat()
    amount_str = str(round(payment.amount, 2))

    return template.format(date=date_str, amount=amount_str)


def _negotiate_live(payment: FailedPayment) -> str:
    import urllib.request

    user_prompt = (
        f"Customer {payment.customer_name} has a failed payment of "
        f"Rs {payment.amount} due {payment.due_date} (reason: {payment.error_code}). "
        f"Write ONE short opening line to them in Hinglish asking when they can pay."
    )
    body = json.dumps({
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 200,
        "system": NEGOTIATION_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": _API_KEY or "",
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return "".join(b.get("text", "") for b in data.get("content", []))
    except Exception as e:
        # Safe fallback on network or API failure: audit-friendly refusal to prevent unguided action
        return f"API_ERROR: Live negotiation call failed ({str(e)}). Customer uncontacted."


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _is_valid_amount(val: Optional[float]) -> bool:
    """Checks that an amount is a finite, positive real number."""
    if val is None:
        return False
    if math.isnan(val) or math.isinf(val):
        return False
    return val > 0.0


def extract(payment: FailedPayment, transcript: str) -> Promise:
    """Second, separate call: pulls a strict-schema outcome from the transcript.
    Enforces strict extraction safety invariants before returning.
    """
    if _MODE == "live":
        return _extract_live(payment, transcript)
    return _extract_simulator(payment, transcript)


def _extract_simulator(payment: FailedPayment, transcript: str) -> Promise:
    lower = transcript.lower()
    
    # 1. Immediate refusal triggers
    if any(stop_word in lower for stop_word in ["stop", "galat", "interest nahi", "call mat karo", "dispute", "wrong number"]):
        return Promise(
            record_id=payment.id,
            status=PromiseStatus.REFUSED,
            promised_date=None,
            promised_amount=None,
            confidence=0.90,
            transcript=transcript,
        )

    # 2. Strict commitment requires both a valid date AND a valid amount
    date_match = _DATE_RE.search(transcript)
    amounts = _AMOUNT_RE.findall(transcript)

    if date_match and amounts:
        try:
            amount = float(amounts[-1])
            date_str = date_match.group(1)
            # Verify date is syntactically valid ISO
            datetime.fromisoformat(date_str)
            
            if _is_valid_amount(amount):
                return Promise(
                    record_id=payment.id,
                    status=PromiseStatus.COMMITTED,
                    promised_date=date_str,
                    promised_amount=amount,
                    confidence=0.85,
                    transcript=transcript,
                )
        except Exception:
            pass

    # 3. Politeness or vague replies ("dekh lenge") default to ACKNOWLEDGED
    return Promise(
        record_id=payment.id,
        status=PromiseStatus.ACKNOWLEDGED,
        promised_date=None,
        promised_amount=None,
        confidence=0.40,
        transcript=transcript,
    )


def _extract_live(payment: FailedPayment, transcript: str) -> Promise:
    import urllib.request

    if transcript.startswith("API_ERROR"):
        return Promise(
            record_id=payment.id,
            status=PromiseStatus.ACKNOWLEDGED,
            promised_date=None,
            promised_amount=None,
            confidence=0.0,
            transcript=transcript,
        )

    body = json.dumps({
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 200,
        "system": EXTRACTION_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"Transcript:\n{transcript}"}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": _API_KEY or "",
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = "".join(b.get("text", "") for b in data.get("content", [])).strip()
        raw = raw.removeprefix("```json").removesuffix("```").strip()
        parsed = json.loads(raw)

        raw_status = str(parsed.get("status", "acknowledged")).lower()
        if raw_status == "committed":
            status = PromiseStatus.COMMITTED
        elif raw_status == "refused":
            status = PromiseStatus.REFUSED
        else:
            status = PromiseStatus.ACKNOWLEDGED

        raw_date = parsed.get("date")
        raw_amount = parsed.get("amount")

        # Validate date and amount
        valid_date = None
        if raw_date and isinstance(raw_date, str):
            try:
                datetime.fromisoformat(raw_date)
                valid_date = raw_date
            except Exception:
                valid_date = None

        valid_amount = None
        if raw_amount is not None:
            try:
                amt = float(raw_amount)
                if _is_valid_amount(amt):
                    valid_amount = amt
            except Exception:
                valid_amount = None

        # Invariant: committed REQUIRES valid date and valid amount
        if status == PromiseStatus.COMMITTED and (valid_date is None or valid_amount is None):
            status = PromiseStatus.ACKNOWLEDGED

        return Promise(
            record_id=payment.id,
            status=status,
            promised_date=valid_date,
            promised_amount=valid_amount,
            confidence=float(parsed.get("confidence", 0.5)),
            transcript=transcript,
        )
    except Exception as e:
        # Fallback safely: model failure or JSON parse error escalates to human review
        return Promise(
            record_id=payment.id,
            status=PromiseStatus.ACKNOWLEDGED,
            promised_date=None,
            promised_amount=None,
            confidence=0.0,
            transcript=f"{transcript} [EXTRACT_ERROR: {str(e)}]",
        )
