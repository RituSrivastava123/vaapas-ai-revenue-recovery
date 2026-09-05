"""Generates synthetic failed-payment records for the benchmark batch.

Deterministic (seeded) so re-runs are comparable. Four categories, each
with realistic error codes a Razorpay-style webhook would actually send.
Contains hidden evaluation metadata that is NEVER passed to the LLM.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import List

from models import FailedPayment, PromiseStatus

NAMES = [
    "Rahul Sharma", "Priya Nair", "Amit Verma", "Sneha Iyer", "Vikram Rao",
    "Anjali Gupta", "Karan Mehta", "Divya Reddy", "Rohan Kapoor", "Neha Joshi",
    "Suresh Pillai", "Meera Krishnan", "Arjun Singh", "Pooja Desai", "Manoj Kumar",
    "Ritu Bose", "Sanjay Menon", "Kavya Shetty", "Aditya Bhat", "Isha Malhotra",
]

CATEGORY_ERROR_CODES = {
    "bank_timeout": ["BANK_GATEWAY_TIMEOUT", "SWITCH_UNAVAILABLE", "ACQUIRER_TIMEOUT"],
    "insufficient_funds": ["INSUFFICIENT_FUNDS", "LIMIT_EXCEEDED", "LOW_BALANCE"],
    "mandate_expired": ["MANDATE_CYCLE_ENDED", "CARD_EXPIRED", "AUTH_EXPIRED"],
    "user_abandoned": ["OTP_TIMEOUT", "USER_DROPPED_2FA", "CHECKOUT_ABANDONED"],
}

CATEGORY_WEIGHTS = {
    "bank_timeout": 0.20,
    "insufficient_funds": 0.35,
    "mandate_expired": 0.25,
    "user_abandoned": 0.20,
}

PERSONAS = [
    "willing_to_pay",
    "salary_delayed",
    "vague_customer",
    "refuses",
    "dispute",
    "stop_requested",
    "fraud",
    "changes_date",
]

# Distribution of personas across synthetic customer interactions
PERSONA_WEIGHTS = {
    "willing_to_pay": 0.25,
    "salary_delayed": 0.20,
    "vague_customer": 0.20,
    "refuses": 0.12,
    "dispute": 0.08,
    "stop_requested": 0.06,
    "fraud": 0.04,
    "changes_date": 0.05,
}


def generate_batch(n: int = 55, seed: int = 42) -> List[FailedPayment]:
    rng = random.Random(seed)
    categories = list(CATEGORY_WEIGHTS.keys())
    weights = list(CATEGORY_WEIGHTS.values())

    persona_list = list(PERSONA_WEIGHTS.keys())
    persona_weights = list(PERSONA_WEIGHTS.values())

    records: List[FailedPayment] = []
    base_date = datetime(2026, 9, 3)

    for i in range(n):
        category = rng.choices(categories, weights=weights, k=1)[0]
        error_code = rng.choice(CATEGORY_ERROR_CODES[category])
        name = rng.choice(NAMES)
        amount = round(rng.uniform(499, 14999), 2)
        due_date = (base_date + timedelta(days=rng.randint(-10, 0))).date().isoformat()
        attempt_count = rng.choice([0, 1, 1, 2])

        persona = rng.choices(persona_list, weights=persona_weights, k=1)[0]
        customer_said_stop = (persona == "stop_requested")
        is_fraud_flagged = (persona == "fraud")

        # Ground truth outcome expected from customer dialogue
        # NOTE: Hidden from LLM / extraction logic; used solely for benchmark verification
        if persona in ("willing_to_pay", "salary_delayed", "changes_date"):
            gt_status = PromiseStatus.COMMITTED
            offset = 3 if persona == "willing_to_pay" else (7 if persona == "salary_delayed" else 5)
            gt_date = (base_date + timedelta(days=offset)).date().isoformat()
            gt_amount = amount
        elif persona in ("refuses", "dispute", "stop_requested", "fraud"):
            gt_status = PromiseStatus.REFUSED
            gt_date = None
            gt_amount = None
        else:  # vague_customer
            gt_status = PromiseStatus.ACKNOWLEDGED
            gt_date = None
            gt_amount = None

        records.append(
            FailedPayment(
                id=f"pay_{i:03d}",
                customer_name=name,
                amount=amount,
                due_date=due_date,
                error_code=error_code,
                attempt_count=attempt_count,
                language_pref="hi-en",
                customer_said_stop=customer_said_stop,
                is_fraud_flagged=is_fraud_flagged,
                ground_truth_status=gt_status,
                ground_truth_date=gt_date,
                ground_truth_amount=gt_amount,
                customer_persona=persona,
            )
        )
    return records


if __name__ == "__main__":
    batch = generate_batch()
    print(f"Generated {len(batch)} synthetic failed-payment records")
    for r in batch[:5]:
        print(r)
