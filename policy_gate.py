"""Deterministic policy gate.

This file contains ZERO LLM calls, by design. It is the only thing in the
pipeline with permission to authorize a retry, a discount, or an
escalation. The negotiation agent can propose; only this module decides.

Every rule here is independently testable -- see tests/test_policy_gate.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Set, Tuple

from models import FailedPayment, Promise, PromiseStatus, GateDecision

MAX_RETRIES = 3
COOLDOWN_HOURS = 4
MAX_DISCOUNT_PCT = 10.0
MAX_CONSECUTIVE_REJECTS = 3


@dataclass
class RetryState:
    """Per-record state the gate tracks across attempts."""
    retry_count: int = 0
    last_attempt_at: Optional[datetime] = None
    consecutive_rejects: int = 0
    stopped: bool = False
    scheduled_idempotency_keys: Set[str] = field(default_factory=set)


class PolicyGate:
    """Stateful gate -- one instance tracks all records in a run."""

    def __init__(self) -> None:
        self._state: Dict[str, RetryState] = {}
        self._executed_idempotency_keys: Set[str] = set()

    def _get_state(self, record_id: str) -> RetryState:
        return self._state.setdefault(record_id, RetryState())

    def evaluate(
        self,
        payment: FailedPayment,
        promise: Promise,
        proposed_discount_pct: float = 0.0,
        now: Optional[datetime] = None,
    ) -> Tuple[GateDecision, str, Optional[str]]:
        """Returns (decision, reason, idempotency_key).
        This is the single authority point for all recovery actions.
        """
        now = now or datetime.now(timezone.utc)
        state = self._get_state(payment.id)

        # --- Hard stop conditions, checked first, unconditionally ---
        transcript_upper = (promise.transcript or "").upper()
        if "STOP" in transcript_upper or "UNSUBSCRIBE" in transcript_upper or "DO NOT CONTACT" in transcript_upper:
            state.stopped = True
            return GateDecision.STOPPED, "Customer explicitly opted out (STOP in transcript) -- contact halted.", None

        if payment.customer_said_stop or state.stopped:
            state.stopped = True
            return GateDecision.STOPPED, "Customer opted out (STOP) -- no further contact permitted.", None

        if payment.is_fraud_flagged:
            state.stopped = True
            return GateDecision.STOPPED, "Fraud signal present -- routed out of recovery flow entirely.", None

        if state.consecutive_rejects >= MAX_CONSECUTIVE_REJECTS:
            state.stopped = True
            return GateDecision.STOPPED, f"{MAX_CONSECUTIVE_REJECTS} consecutive rejections -- stopping to avoid harassment.", None

        # --- Defensive discount ceiling ---
        if proposed_discount_pct > MAX_DISCOUNT_PCT:
            return (
                GateDecision.REJECTED_INVALID,
                f"Proposed discount {proposed_discount_pct}% exceeds hard ceiling of {MAX_DISCOUNT_PCT}%.",
                None,
            )

        # --- Promise status routing ---
        if promise.status == PromiseStatus.REFUSED:
            state.consecutive_rejects += 1
            if state.consecutive_rejects >= MAX_CONSECUTIVE_REJECTS:
                state.stopped = True
                return GateDecision.STOPPED, f"{MAX_CONSECUTIVE_REJECTS} consecutive rejections reached -- stopping.", None
            return GateDecision.ESCALATED_HUMAN, "Customer refused -- routed to human review, not retried automatically.", None

        if promise.status == PromiseStatus.ACKNOWLEDGED:
            # Politeness without a specific commitment is never assumed to be a promise.
            return GateDecision.ESCALATED_HUMAN, "No clear commitment captured -- routed to human review rather than assumed.", None

        # promise.status == COMMITTED from here on
        # Strict syntactic and financial validation:
        if not promise.promised_date or not isinstance(promise.promised_date, str):
            return (
                GateDecision.REJECTED_INVALID,
                "Marked committed but missing a concrete date -- rejected, not trusted.",
                None,
            )

        try:
            datetime.fromisoformat(promise.promised_date)
        except Exception:
            return (
                GateDecision.REJECTED_INVALID,
                f"Invalid date format '{promise.promised_date}' -- rejected, not trusted.",
                None,
            )

        if promise.promised_amount is None:
            return (
                GateDecision.REJECTED_INVALID,
                "Marked committed but missing a concrete amount -- rejected, not trusted.",
                None,
            )

        try:
            amt = float(promise.promised_amount)
        except (ValueError, TypeError):
            return (
                GateDecision.REJECTED_INVALID,
                "Promised amount is not a valid number -- rejected.",
                None,
            )

        if math.isnan(amt) or math.isinf(amt):
            return (
                GateDecision.REJECTED_INVALID,
                "Promised amount is NaN or Infinity -- rejected.",
                None,
            )

        if amt <= 0.0:
            return (
                GateDecision.REJECTED_INVALID,
                f"Promised amount {amt} is zero or negative -- rejected.",
                None,
            )

        if amt > payment.amount * 1.0001:
            return (
                GateDecision.REJECTED_INVALID,
                f"Promised amount ({amt}) exceeds outstanding debt ({payment.amount}) -- rejected as implausible.",
                None,
            )

        if state.retry_count >= MAX_RETRIES:
            state.stopped = True
            return GateDecision.STOPPED, f"Max retries ({MAX_RETRIES}) reached for this record.", None

        if state.last_attempt_at is not None:
            elapsed = now - state.last_attempt_at
            if elapsed < timedelta(hours=COOLDOWN_HOURS):
                return (
                    GateDecision.REJECTED_INVALID,
                    f"Cooldown not elapsed ({elapsed} < {COOLDOWN_HOURS}h) -- retry blocked.",
                    None,
                )

        # All checks passed -- generate deterministic idempotency key and authorize retry
        state.retry_count += 1
        state.last_attempt_at = now
        idempotency_key = f"{payment.id}_att{state.retry_count}_{promise.promised_date}_{amt:.2f}"
        state.scheduled_idempotency_keys.add(idempotency_key)

        return (
            GateDecision.RETRY_SCHEDULED,
            f"Retry #{state.retry_count} authorized for {promise.promised_date} at Rs {amt:.2f}.",
            idempotency_key,
        )

    def execute_downstream_retry(self, idempotency_key: str) -> Tuple[bool, str]:
        """Enforces downstream execution idempotency.
        Prevents double-counting or double-debiting even if a webhook or scheduler fires twice.
        """
        if not idempotency_key:
            return False, "Missing idempotency key."
        if idempotency_key in self._executed_idempotency_keys:
            return False, f"Duplicate execution blocked: key '{idempotency_key}' already executed."
        self._executed_idempotency_keys.add(idempotency_key)
        return True, f"Execution authorized for key '{idempotency_key}'."
