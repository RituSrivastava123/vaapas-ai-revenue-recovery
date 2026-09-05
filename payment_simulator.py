"""Deterministic Payment Outcome Simulator & Naive Fixed Retry Baseline.

Models synthetic post-decision payment resolution based on defined simulation rules.
NOTE: These are explicitly documented SIMULATION ASSUMPTIONS, NOT production statistics.
All outcomes are generated deterministically and reproducibly.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from models import FailedPayment, FailureCategory, PromiseStatus


class PaymentOutcomeSimulator:
    """Simulates whether an authorized retry attempt successfully collects funds.
    Receives ONLY information legitimately available at execution time.
    Does NOT receive hidden ground-truth or negotiation simulator state.
    """

    @staticmethod
    def simulate_outcome(
        payment: FailedPayment,
        scheduled_date: str,
        scheduled_amount: float,
        promised_date: Optional[str] = None,
        promise_status: Optional[PromiseStatus] = None,
        category: Optional[FailureCategory] = None,
        seed: Optional[int] = None,
    ) -> Tuple[bool, float, str]:
        """Returns (is_recovered, recovered_amount, outcome_reason)."""
        # Hard stops: customer opt-out, fraud, refusal
        if payment.customer_said_stop:
            return False, 0.0, "Customer opted out (STOP). Zero recovery."
        if payment.is_fraud_flagged:
            return False, 0.0, "Fraud flag. Zero recovery."
        if promise_status == PromiseStatus.REFUSED:
            return False, 0.0, "Customer refused payment. Zero recovery."

        # Mandate expired cannot be recovered by simple debit (needs customer re-auth)
        if category == FailureCategory.MANDATE_EXPIRED or payment.error_code in (
            "MANDATE_CYCLE_ENDED", "CARD_EXPIRED", "AUTH_EXPIRED"
        ):
            return False, 0.0, "Mandate expired. Requires re-authorization token; debit rejected."

        # Deterministic per-record seed so outcomes never fluctuate across runs
        rec_seed = (
            seed
            if seed is not None
            else int(hashlib.sha256(f"{payment.id}_outcome".encode("utf-8")).hexdigest()[:8], 16)
        )
        rng = random.Random(rec_seed)

        # Base recovery probabilities under simulation assumptions
        if category == FailureCategory.BANK_TIMEOUT or payment.error_code in (
            "BANK_GATEWAY_TIMEOUT", "SWITCH_UNAVAILABLE", "ACQUIRER_TIMEOUT"
        ):
            # Transient infrastructure degradation: high success rate post cooldown
            prob = 0.72
            reason = "Bank switch cleared after cooldown period."
        elif category == FailureCategory.INSUFFICIENT_FUNDS or payment.error_code in (
            "INSUFFICIENT_FUNDS", "LIMIT_EXCEEDED", "LOW_BALANCE"
        ):
            # Liquidity timing bottleneck:
            if promised_date and scheduled_date >= promised_date:
                prob = 0.70
                reason = "Retry executed on/after customer salary credit promise."
            elif promised_date and scheduled_date < promised_date:
                prob = 0.15
                reason = "Retry executed prematurely before customer indicated funds available."
            else:
                prob = 0.20
                reason = "Untimed retry on low balance account."
        elif category == FailureCategory.USER_ABANDONED or payment.error_code in (
            "OTP_TIMEOUT", "USER_DROPPED_2FA", "CHECKOUT_ABANDONED"
        ):
            if promise_status == PromiseStatus.COMMITTED:
                prob = 0.65
                reason = "Customer intent reaffirmed after drop-off."
            else:
                prob = 0.25
                reason = "Unassisted abandoned checkout."
        else:
            prob = 0.20
            reason = "Default retry heuristic."

        # Prior failed attempts penalty: repeated fails indicate chronic issues
        if payment.attempt_count > 0:
            prob = max(0.05, prob - (0.07 * payment.attempt_count))

        roll = rng.random()
        if roll < prob:
            return True, scheduled_amount, f"SUCCESS: {reason} (p={prob:.2f})"
        else:
            return False, 0.0, f"FAILED: Insufficient balance or bank decline (p={prob:.2f})"


class FixedRetryBaseline:
    """Standard naive industry baseline:
    - No conversation
    - No personalized promise
    - Naive fixed retry after 24 hours
    - Evaluated on the EXACT same synthetic population and outcome simulator
    """

    @staticmethod
    def run(batch: List[FailedPayment]) -> dict:
        total_at_risk = sum(p.amount for p in batch)
        recovered_amount = 0.0
        recovered_count = 0

        base_date = datetime(2026, 9, 3)

        for payment in batch:
            # Naive retry executes 24 hours after base failure without conversation
            naive_scheduled_date = (base_date + timedelta(days=1)).date().isoformat()

            is_rec, rec_amt, _ = PaymentOutcomeSimulator.simulate_outcome(
                payment=payment,
                scheduled_date=naive_scheduled_date,
                scheduled_amount=payment.amount,
                promised_date=None,
                promise_status=None,
                category=None,
            )
            if is_rec:
                recovered_count += 1
                recovered_amount += rec_amt

        recovery_rate = (recovered_amount / total_at_risk) if total_at_risk > 0 else 0.0
        return {
            "baseline_recovered_amount": round(recovered_amount, 2),
            "baseline_recovery_rate": round(recovery_rate, 4),
            "baseline_recovered_count": recovered_count,
        }
