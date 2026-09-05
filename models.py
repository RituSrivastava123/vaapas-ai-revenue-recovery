"""Core data models used across the Vaapas pipeline.

Kept deliberately small and explicit: every field here is read or written
by at least one module. No speculative fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class FailureCategory(str, Enum):
    BANK_TIMEOUT = "bank_timeout"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    MANDATE_EXPIRED = "mandate_expired"
    USER_ABANDONED = "user_abandoned"


class PromiseStatus(str, Enum):
    ACKNOWLEDGED = "acknowledged"   # customer responded, but no clear commitment
    COMMITTED = "committed"          # customer gave a specific date + amount
    REFUSED = "refused"              # customer declined / disputed the charge


class GateDecision(str, Enum):
    RETRY_SCHEDULED = "retry_scheduled"
    ESCALATED_HUMAN = "escalated_human"
    STOPPED = "stopped"
    REJECTED_INVALID = "rejected_invalid"


@dataclass
class FailedPayment:
    id: str
    customer_name: str
    amount: float
    due_date: str
    error_code: str
    attempt_count: int
    language_pref: str = "hi-en"     # Hinglish by default
    customer_said_stop: bool = False
    is_fraud_flagged: bool = False
    
    # Hidden evaluation ground-truth (NEVER passed into negotiation or extraction logic)
    ground_truth_status: PromiseStatus = PromiseStatus.ACKNOWLEDGED
    ground_truth_date: Optional[str] = None
    ground_truth_amount: Optional[float] = None
    customer_persona: str = "vague_customer"


@dataclass
class Diagnosis:
    record_id: str
    category: FailureCategory
    confidence: float
    rationale: str


@dataclass
class Promise:
    record_id: str
    status: PromiseStatus
    promised_date: Optional[str]
    promised_amount: Optional[float]
    confidence: float
    transcript: str = ""


@dataclass
class AuditEntry:
    timestamp: str
    record_id: str
    actor: str            # "rca" | "negotiation" | "gate"
    decision: str
    detail: str
    attempt_number: int = 1
    idempotency_key: Optional[str] = None

    @staticmethod
    def now(
        record_id: str,
        actor: str,
        decision: str,
        detail: str,
        attempt_number: int = 1,
        idempotency_key: Optional[str] = None,
    ) -> "AuditEntry":
        return AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
            record_id=record_id,
            actor=actor,
            decision=decision,
            detail=detail,
            attempt_number=attempt_number,
            idempotency_key=idempotency_key,
        )
