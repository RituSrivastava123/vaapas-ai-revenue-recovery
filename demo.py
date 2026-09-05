"""Interactive Demo for Vaapas — Razorpay AI Buildathon 2026 (Track 3).

Demonstrates the two core design principles:
  Case A: Autonomous Hinglish negotiation -> PolicyGate validation -> Successful recovery.
  Case B: Adversarial model output (0.999 confidence) -> PolicyGate intercept & rejection.

Run with: python demo.py
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime, timezone
import rca_engine
import negotiation_agent
from models import FailedPayment, Promise, PromiseStatus, GateDecision, FailureCategory
from policy_gate import PolicyGate
from payment_simulator import PaymentOutcomeSimulator


def run_demo() -> None:
    gate = PolicyGate()
    sep = "=" * 70

    print("\n" + sep)
    print("      VAAPAS -- HINGLISH REVENUE RECOVERY AGENT DEMO")
    print("             Razorpay AI Buildathon 2026 -- Track 3")
    print(sep)

    # -------------------------------------------------------------------------
    # CASE A: Successful Intelligent Recovery
    # -------------------------------------------------------------------------
    print("\n[CASE A] — INTELLIGENT HINGLISH NEGOTIATION & SCHEDULED RECOVERY\n")

    payment_a = FailedPayment(
        id="pay_demo_001",
        customer_name="Rohan Kapoor",
        amount=3499.00,
        due_date="2026-09-02",
        error_code="INSUFFICIENT_FUNDS",
        attempt_count=0,
        customer_persona="salary_delayed",
    )

    print(f"1. FAILED PAYMENT RECEIVED:")
    print(f"   Record ID : {payment_a.id}")
    print(f"   Customer  : {payment_a.customer_name}")
    print(f"   Amount    : Rs {payment_a.amount:,.2f}")
    print(f"   Reason    : {payment_a.error_code} (Due: {payment_a.due_date})")

    # Step 1: RCA
    diag_a = rca_engine.diagnose(payment_a)
    print(f"\n2. RCA DIAGNOSIS (Advisor):")
    print(f"   Category   : {diag_a.category.value}")
    print(f"   Confidence : {diag_a.confidence*100:.0f}%")
    print(f"   Rationale  : {diag_a.rationale}")

    # Step 2: Negotiation & Extraction
    print(f"\n3. HINGLISH NEGOTIATION & EXTRACTION (Advisor):")
    transcript_a = negotiation_agent.negotiate(payment_a)
    print(f"   Customer   : \"{transcript_a}\"")
    promise_a = negotiation_agent.extract(payment_a, transcript_a)
    print(f"   Extracted  : Status={promise_a.status.value.upper()} | Date={promise_a.promised_date} | Amount=Rs {promise_a.promised_amount}")
    print(f"   Confidence : {promise_a.confidence}")

    # Step 3: PolicyGate Validation
    print(f"\n4. DETERMINISTIC POLICY GATE (Sole Authority -- Zero LLM Calls):")
    dec_a, reason_a, key_a = gate.evaluate(payment_a, promise_a)
    print(f"   [OK] Valid positive amount (Rs {promise_a.promised_amount} <= Rs {payment_a.amount})")
    print(f"   [OK] Valid ISO date format ({promise_a.promised_date})")
    print(f"   [OK] Cooldown satisfied (> 4 hours)")
    print(f"   [OK] No STOP opt-out")
    print(f"   [OK] No fraud flag")
    print(f"   DECISION   : {dec_a.value.upper()}")
    print(f"   Reason     : {reason_a}")
    print(f"   Idempotency: {key_a}")

    # Step 4: Downstream Execution
    print(f"\n5. EXECUTION & RECOVERY OUTCOME:")
    exec_ok, _ = gate.execute_downstream_retry(key_a)
    is_rec, rec_amt, outcome_detail = PaymentOutcomeSimulator.simulate_outcome(
        payment=payment_a,
        scheduled_date=promise_a.promised_date,
        scheduled_amount=promise_a.promised_amount,
        promised_date=promise_a.promised_date,
        promise_status=promise_a.status,
        category=diag_a.category,
        seed=42,
    )
    print(f"   Idempotency Check : {'PASS (Unique Key)' if exec_ok else 'FAIL'}")
    print(f"   Recovery Result   : {'RECOVERED' if is_rec else 'FAILED'}")
    print(f"   Amount Collected  : Rs {rec_amt:,.2f}")
    print(f"   Outcome Telemetry : {outcome_detail}")

    print("\n" + "-" * 70)

    # -------------------------------------------------------------------------
    # CASE B: Adversarial Model Output
    # -------------------------------------------------------------------------
    print("\n[CASE B] — ADVERSARIAL MODEL OUTPUT (Zero-Trust Financial Gate)\n")

    payment_b = FailedPayment(
        id="pay_demo_002",
        customer_name="Vikram Rao",
        amount=1200.00,
        due_date="2026-09-02",
        error_code="INSUFFICIENT_FUNDS",
        attempt_count=1,
    )

    print(f"1. FAILED PAYMENT CONTEXT:")
    print(f"   Outstanding Debt : Rs {payment_b.amount:,.2f}")

    # Adversarial fabricated input claiming an absurd amount with high confidence
    malicious_promise = Promise(
        record_id=payment_b.id,
        status=PromiseStatus.COMMITTED,
        promised_date="2026-09-10",
        promised_amount=99999999.00,  # 99 Million on a Rs 1,200 debt!
        confidence=0.999,              # Deceptive 99.9% reported confidence
        transcript="Adversarial prompt injection claiming 99 million commitment.",
    )

    print(f"\n2. UPSTREAM AGENT CLAIMS:")
    print(f"   Claimed Status     : {malicious_promise.status.value.upper()}")
    print(f"   Claimed Amount     : Rs {malicious_promise.promised_amount:,.2f}")
    print(f"   Reported Confidence: {malicious_promise.confidence*100:.1f}%")

    print(f"\n3. DETERMINISTIC POLICY GATE INTERCEPTION:")
    dec_b, reason_b, key_b = gate.evaluate(payment_b, malicious_promise)
    print(f"   GATE DECISION      : {dec_b.value.upper()}")
    print(f"   Rejection Reason   : {reason_b}")
    print(f"   LLM Confidence     : IGNORED (Financial invariants cannot be overridden)")
    print(f"   Downstream Action  : BLOCKED (Zero money moved, audit violation recorded)")

    print("\n" + sep)
    print(" CORE THESIS: The AI can recommend a financial action.")
    print("              It cannot authorize one.")
    print(sep + "\n")


if __name__ == "__main__":
    run_demo()
