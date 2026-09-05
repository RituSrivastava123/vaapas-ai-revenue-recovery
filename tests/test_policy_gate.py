"""Comprehensive test suite for policy_gate.py.

Proves every policy invariant cannot be bypassed by an LLM or adversarial inputs.
Target: 20+ tests covering statefulness, boundary validation, and idempotency.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import FailedPayment, Promise, PromiseStatus, GateDecision
from policy_gate import PolicyGate, MAX_RETRIES, COOLDOWN_HOURS, MAX_DISCOUNT_PCT


def _payment(**kwargs) -> FailedPayment:
    defaults = dict(
        id="pay_test",
        customer_name="Test User",
        amount=1000.0,
        due_date="2026-09-01",
        error_code="LOW_BALANCE",
        attempt_count=0,
    )
    defaults.update(kwargs)
    return FailedPayment(**defaults)


def _committed_promise(amount: float = 1000.0, date: str = "2026-09-10", record_id: str = "pay_test", transcript: str = "") -> Promise:
    return Promise(record_id, PromiseStatus.COMMITTED, date, amount, 0.90, transcript)


# --- 1. Basic Routing Invariants ---

def test_01_valid_commitment_scheduled():
    """Valid commitment within limits authorizes a scheduled retry."""
    gate = PolicyGate()
    payment = _payment()
    promise = _committed_promise()
    decision, reason, key = gate.evaluate(payment, promise)
    assert decision == GateDecision.RETRY_SCHEDULED
    assert key is not None
    assert "authorized" in reason.lower()


def test_02_vague_acknowledgement_escalated():
    """Politeness without commitment ('dekh lenge') is escalated to human, not scheduled."""
    gate = PolicyGate()
    payment = _payment()
    promise = Promise("pay_test", PromiseStatus.ACKNOWLEDGED, None, None, 0.4, "dekh lenge")
    decision, reason, key = gate.evaluate(payment, promise)
    assert decision == GateDecision.ESCALATED_HUMAN
    assert key is None


def test_03_refusal_escalated():
    """Explicit customer refusal routes to human review, not automated debit."""
    gate = PolicyGate()
    payment = _payment()
    promise = Promise("pay_test", PromiseStatus.REFUSED, None, None, 0.85, "main nahi dunga")
    decision, reason, key = gate.evaluate(payment, promise)
    assert decision == GateDecision.ESCALATED_HUMAN
    assert key is None


# --- 2. Hard Stop Conditions ---

def test_04_stop_flag_stopped():
    """Customer-level STOP flag immediately stops recovery."""
    gate = PolicyGate()
    payment = _payment(customer_said_stop=True)
    promise = _committed_promise()
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.STOPPED
    assert "opted out" in reason.lower() or "stop" in reason.lower()


def test_05_stop_transcript_stopped():
    """Customer saying STOP in conversation halts contact even if model marks COMMITTED."""
    gate = PolicyGate()
    payment = _payment()
    promise = _committed_promise(transcript="Please STOP calling me, unsubscribe!")
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.STOPPED
    assert "stop" in reason.lower()


def test_06_fraud_flag_stopped():
    """Fraud indicator blocks recovery entirely."""
    gate = PolicyGate()
    payment = _payment(is_fraud_flagged=True)
    promise = _committed_promise()
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.STOPPED
    assert "fraud" in reason.lower()


def test_07_max_retries_stopped():
    """Retry count cannot exceed MAX_RETRIES (3 attempts)."""
    gate = PolicyGate()
    payment = _payment(id="pay_retry_cap")
    now = datetime.now(timezone.utc)

    for i in range(MAX_RETRIES):
        promise = _committed_promise(record_id="pay_retry_cap")
        decision, _, _ = gate.evaluate(payment, promise, now=now)
        assert decision == GateDecision.RETRY_SCHEDULED
        now += timedelta(hours=COOLDOWN_HOURS + 1)

    # Attempt 4 must be stopped
    promise = _committed_promise(record_id="pay_retry_cap")
    decision, reason, _ = gate.evaluate(payment, promise, now=now)
    assert decision == GateDecision.STOPPED
    assert "max retries" in reason.lower()


def test_08_retry_after_max_attempts_remains_stopped():
    """Once a record is stopped due to retry exhaustion, subsequent calls remain stopped."""
    gate = PolicyGate()
    payment = _payment(id="pay_locked")
    now = datetime.now(timezone.utc)

    for _ in range(MAX_RETRIES):
        gate.evaluate(payment, _committed_promise(record_id="pay_locked"), now=now)
        now += timedelta(hours=COOLDOWN_HOURS + 1)

    # Next attempt stops
    decision, _, _ = gate.evaluate(payment, _committed_promise(record_id="pay_locked"), now=now)
    assert decision == GateDecision.STOPPED

    # Further attempt with a fresh date still remains stopped
    future_now = now + timedelta(days=5)
    decision2, _, _ = gate.evaluate(payment, _committed_promise(record_id="pay_locked"), now=future_now)
    assert decision2 == GateDecision.STOPPED


# --- 3. Cooldown Invariants ---

def test_09_cooldown_violation_rejected():
    """A retry inside the 4-hour cooldown window must be rejected."""
    gate = PolicyGate()
    payment = _payment(id="pay_cool")
    now = datetime.now(timezone.utc)
    promise = _committed_promise(record_id="pay_cool")
    decision, _, _ = gate.evaluate(payment, promise, now=now)
    assert decision == GateDecision.RETRY_SCHEDULED

    too_soon = now + timedelta(hours=1)
    decision2, reason2, _ = gate.evaluate(payment, promise, now=too_soon)
    assert decision2 == GateDecision.REJECTED_INVALID
    assert "cooldown" in reason2.lower()


def test_10_cooldown_satisfied_allows_retry():
    """A retry after the 4-hour cooldown window has elapsed is allowed."""
    gate = PolicyGate()
    payment = _payment(id="pay_cool_ok")
    now = datetime.now(timezone.utc)
    promise = _committed_promise(record_id="pay_cool_ok")
    decision1, _, _ = gate.evaluate(payment, promise, now=now)
    assert decision1 == GateDecision.RETRY_SCHEDULED

    after_cooldown = now + timedelta(hours=4, minutes=5)
    decision2, _, _ = gate.evaluate(payment, promise, now=after_cooldown)
    assert decision2 == GateDecision.RETRY_SCHEDULED


# --- 4. Syntactic & Numerical Validation ---

def test_11_missing_date_rejected():
    """Committed status with missing date must be rejected."""
    gate = PolicyGate()
    payment = _payment()
    promise = Promise("pay_test", PromiseStatus.COMMITTED, None, 1000.0, 0.9)
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.REJECTED_INVALID
    assert "date" in reason.lower()


def test_12_malformed_date_rejected():
    """Committed status with unparseable date must be rejected."""
    gate = PolicyGate()
    payment = _payment()
    promise = Promise("pay_test", PromiseStatus.COMMITTED, "tomorrow_morning", 1000.0, 0.9)
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.REJECTED_INVALID
    assert "invalid date" in reason.lower()


def test_13_missing_amount_rejected():
    """Committed status with missing amount must be rejected."""
    gate = PolicyGate()
    payment = _payment()
    promise = Promise("pay_test", PromiseStatus.COMMITTED, "2026-09-10", None, 0.9)
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.REJECTED_INVALID
    assert "amount" in reason.lower()


def test_14_zero_amount_rejected():
    """Committed status with zero amount must be rejected."""
    gate = PolicyGate()
    payment = _payment()
    promise = _committed_promise(amount=0.0)
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.REJECTED_INVALID
    assert "zero or negative" in reason.lower()


def test_15_negative_amount_rejected():
    """Committed status with negative amount must be rejected."""
    gate = PolicyGate()
    payment = _payment()
    promise = _committed_promise(amount=-500.0)
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.REJECTED_INVALID
    assert "zero or negative" in reason.lower()


def test_16_nan_amount_rejected():
    """Committed status with NaN amount must be rejected."""
    gate = PolicyGate()
    payment = _payment()
    promise = _committed_promise(amount=float("nan"))
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.REJECTED_INVALID
    assert "nan" in reason.lower()


def test_17_inf_amount_rejected():
    """Committed status with Infinity amount must be rejected."""
    gate = PolicyGate()
    payment = _payment()
    promise = _committed_promise(amount=float("inf"))
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.REJECTED_INVALID
    assert "nan or infinity" in reason.lower()


def test_18_amount_exceeding_debt_rejected():
    """Committed amount exceeding the actual outstanding debt is rejected as implausible."""
    gate = PolicyGate()
    payment = _payment(amount=1000.0)
    promise = _committed_promise(amount=1500.0)
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.REJECTED_INVALID
    assert "exceeds" in reason.lower()


# --- 5. Defenses & Adversarial Tests ---

def test_19_consecutive_rejections_trigger_stop():
    """Repeated explicit refusals (3 times) trigger an automatic stop."""
    gate = PolicyGate()
    payment = _payment(id="pay_repeat_refusal")
    refused = Promise("pay_repeat_refusal", PromiseStatus.REFUSED, None, None, 0.9)

    for _ in range(2):
        dec, _, _ = gate.evaluate(payment, refused)
        assert dec == GateDecision.ESCALATED_HUMAN

    # Third consecutive refusal must transition to STOPPED
    dec3, reason3, _ = gate.evaluate(payment, refused)
    assert dec3 == GateDecision.STOPPED
    assert "consecutive" in reason3.lower()


def test_20_discount_ceiling_cannot_be_exceeded():
    """Proposed discount exceeding MAX_DISCOUNT_PCT (10%) is blocked."""
    gate = PolicyGate()
    payment = _payment()
    promise = _committed_promise()
    decision, reason, _ = gate.evaluate(payment, promise, proposed_discount_pct=MAX_DISCOUNT_PCT + 5.0)
    assert decision == GateDecision.REJECTED_INVALID
    assert str(MAX_DISCOUNT_PCT) in reason


def test_21_adversarial_confidence_099_rejected():
    """Adversarial case: LLM reports 0.999 confidence on an impossible amount. Gate rejects it."""
    gate = PolicyGate()
    payment = _payment(amount=1000.0)
    fabricated = Promise(
        record_id="pay_test",
        status=PromiseStatus.COMMITTED,
        promised_date="2026-09-10",
        promised_amount=99999999.0,
        confidence=0.999,
        transcript="Trust me, customer confirmed 99 million.",
    )
    decision, reason, _ = gate.evaluate(payment, fabricated)
    assert decision == GateDecision.REJECTED_INVALID
    assert "exceeds" in reason.lower()


def test_22_stop_plus_valid_commitment_stop_wins():
    """If customer said STOP, even a valid commitment is blocked by STOP."""
    gate = PolicyGate()
    payment = _payment(customer_said_stop=True)
    promise = _committed_promise(amount=1000.0, date="2026-09-10")
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.STOPPED
    assert "opted out" in reason.lower() or "stop" in reason.lower()


def test_23_fraud_plus_valid_commitment_stop_wins():
    """If fraud flagged, valid commitment is blocked by fraud stop."""
    gate = PolicyGate()
    payment = _payment(is_fraud_flagged=True)
    promise = _committed_promise(amount=1000.0, date="2026-09-10")
    decision, reason, _ = gate.evaluate(payment, promise)
    assert decision == GateDecision.STOPPED
    assert "fraud" in reason.lower()


def test_24_idempotency_duplicate_execution_prevented():
    """Downstream execution rejects duplicate idempotency keys, preventing double-debiting."""
    gate = PolicyGate()
    payment = _payment(id="pay_idem")
    promise = _committed_promise(record_id="pay_idem")
    decision, _, key = gate.evaluate(payment, promise)
    assert decision == GateDecision.RETRY_SCHEDULED
    assert key is not None

    # First execution succeeds
    ok1, msg1 = gate.execute_downstream_retry(key)
    assert ok1 is True
    assert "authorized" in msg1.lower()

    # Second execution of the identical key is blocked as duplicate
    ok2, msg2 = gate.execute_downstream_retry(key)
    assert ok2 is False
    assert "duplicate" in msg2.lower()


# --- 6. Verification: Ground Truth Isolation ---

def test_25_ground_truth_not_leaked_to_model_inputs():
    """Confirms that hidden benchmark ground-truth fields are isolated and never leaked to inference."""
    import inspect
    import negotiation_agent

    # Inspect negotiate and extract signatures and implementations
    negotiate_src = inspect.getsource(negotiation_agent.negotiate)
    extract_src = inspect.getsource(negotiation_agent.extract)
    extract_sim_src = inspect.getsource(negotiation_agent._extract_simulator)
    extract_live_src = inspect.getsource(negotiation_agent._extract_live)

    for src in [negotiate_src, extract_src, extract_sim_src, extract_live_src]:
        assert "ground_truth_status" not in src
        assert "ground_truth_date" not in src
        assert "ground_truth_amount" not in src

    # Verify that a payment object with mismatched ground truth does not bias extraction
    dummy_payment = _payment(amount=1000.0)
    dummy_payment.ground_truth_status = PromiseStatus.REFUSED
    dummy_payment.ground_truth_date = None
    dummy_payment.ground_truth_amount = None

    # Transcript has an explicit commitment: extraction must parse the transcript text alone
    sample_transcript = "Haan bhaiya, 2026-09-12 tak pura 1000.0 pay kar dunga."
    promise = negotiation_agent.extract(dummy_payment, sample_transcript)

    # Extraction succeeds based on transcript, proving it did NOT use dummy_payment.ground_truth_status
    assert promise.status == PromiseStatus.COMMITTED
    assert promise.promised_date == "2026-09-12"
    assert promise.promised_amount == 1000.0
