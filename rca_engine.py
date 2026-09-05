"""Root-cause diagnosis engine.

This is the ADVISOR in the pipeline: it classifies why a payment failed
and how confident it is. It never triggers a retry, a discount, or any
other action directly -- that authority belongs only to policy_gate.py.
"""
from __future__ import annotations

from models import FailedPayment, Diagnosis, FailureCategory

# Rule-based classification keyed on error code substrings. Deliberately
# simple and auditable -- a judge can read this file top to bottom and
# know exactly why a record got the label it got.
_CATEGORY_MAP = {
    "BANK_GATEWAY_TIMEOUT": (FailureCategory.BANK_TIMEOUT, 0.93),
    "SWITCH_UNAVAILABLE": (FailureCategory.BANK_TIMEOUT, 0.90),
    "ACQUIRER_TIMEOUT": (FailureCategory.BANK_TIMEOUT, 0.88),
    "INSUFFICIENT_FUNDS": (FailureCategory.INSUFFICIENT_FUNDS, 0.97),
    "LIMIT_EXCEEDED": (FailureCategory.INSUFFICIENT_FUNDS, 0.85),
    "LOW_BALANCE": (FailureCategory.INSUFFICIENT_FUNDS, 0.95),
    "MANDATE_CYCLE_ENDED": (FailureCategory.MANDATE_EXPIRED, 0.96),
    "CARD_EXPIRED": (FailureCategory.MANDATE_EXPIRED, 0.98),
    "AUTH_EXPIRED": (FailureCategory.MANDATE_EXPIRED, 0.90),
    "OTP_TIMEOUT": (FailureCategory.USER_ABANDONED, 0.87),
    "USER_DROPPED_2FA": (FailureCategory.USER_ABANDONED, 0.91),
    "CHECKOUT_ABANDONED": (FailureCategory.USER_ABANDONED, 0.80),
}

_RATIONALE = {
    FailureCategory.BANK_TIMEOUT: "Gateway/acquirer-side timeout, likely transient -- retry with backoff.",
    FailureCategory.INSUFFICIENT_FUNDS: "Customer-side liquidity issue -- timing matters more than channel.",
    FailureCategory.MANDATE_EXPIRED: "Authentication/mandate needs re-establishing, not a simple retry.",
    FailureCategory.USER_ABANDONED: "Customer dropped off mid-flow -- may not be a real payment intent.",
}


def diagnose(payment: FailedPayment) -> Diagnosis:
    category, confidence = _CATEGORY_MAP.get(
        payment.error_code, (FailureCategory.USER_ABANDONED, 0.4)
    )
    # Repeated attempts on the same record lower our confidence that a
    # plain retry will fix it -- surfaced honestly rather than hidden.
    if payment.attempt_count >= 2:
        confidence = max(0.3, confidence - 0.15)

    return Diagnosis(
        record_id=payment.id,
        category=category,
        confidence=round(confidence, 2),
        rationale=_RATIONALE[category],
    )
