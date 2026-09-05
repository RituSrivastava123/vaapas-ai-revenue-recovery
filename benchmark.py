"""Runs the synthetic batch through the pipeline and computes metrics.

Every number printed here is computed directly from the run, not hardcoded.
Re-running this script re-computes everything deterministically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import rca_engine
import negotiation_agent
from audit_log import AuditLog
from models import AuditEntry, FailedPayment, GateDecision, PromiseStatus, FailureCategory
from policy_gate import PolicyGate
from payment_simulator import PaymentOutcomeSimulator, FixedRetryBaseline
from synthetic_data import generate_batch


@dataclass
class RunResult:
    record_id: str
    category: str
    promise_status: str
    gate_decision: str
    gate_reason: str
    idempotency_key: Optional[str]
    is_recovered: bool
    recovered_amount: float
    ground_truth_status: str


def run_pipeline(batch: List[FailedPayment], log: AuditLog) -> List[RunResult]:
    gate = PolicyGate()
    results: List[RunResult] = []

    for payment in batch:
        # Step 1: RCA Diagnosis (Advisor)
        diagnosis = rca_engine.diagnose(payment)
        log.write(AuditEntry.now(
            record_id=payment.id,
            actor="rca",
            decision=f"category={diagnosis.category.value}",
            detail=f"confidence={diagnosis.confidence} rationale={diagnosis.rationale}",
            attempt_number=payment.attempt_count + 1,
        ))

        # Step 2: Negotiation & Extraction (Advisor)
        transcript = negotiation_agent.negotiate(payment)
        promise = negotiation_agent.extract(payment, transcript)
        log.write(AuditEntry.now(
            record_id=payment.id,
            actor="negotiation",
            decision=f"status={promise.status.value}",
            detail=f"date={promise.promised_date} amount={promise.promised_amount} confidence={promise.confidence}",
            attempt_number=payment.attempt_count + 1,
        ))

        # Step 3: PolicyGate Deterministic FSM Evaluation (Sole Financial Authority)
        decision, reason, idempotency_key = gate.evaluate(payment, promise)

        # Audit decision is committed to disk BEFORE any downstream execution
        log.write(AuditEntry.now(
            record_id=payment.id,
            actor="gate",
            decision=decision.value,
            detail=reason,
            attempt_number=payment.attempt_count + 1,
            idempotency_key=idempotency_key,
        ))

        # Step 4: Downstream Execution & Outcome Simulation
        is_rec = False
        rec_amt = 0.0
        if decision == GateDecision.RETRY_SCHEDULED and idempotency_key:
            exec_ok, _ = gate.execute_downstream_retry(idempotency_key)
            if exec_ok and promise.promised_date and promise.promised_amount:
                is_rec, rec_amt, outcome_detail = PaymentOutcomeSimulator.simulate_outcome(
                    payment=payment,
                    scheduled_date=promise.promised_date,
                    scheduled_amount=promise.promised_amount,
                    promised_date=promise.promised_date,
                    promise_status=promise.status,
                    category=diagnosis.category,
                )
                log.write(AuditEntry.now(
                    record_id=payment.id,
                    actor="scheduler",
                    decision="EXEC_SUCCESS" if is_rec else "EXEC_FAILED",
                    detail=outcome_detail,
                    attempt_number=payment.attempt_count + 1,
                    idempotency_key=idempotency_key,
                ))

        results.append(RunResult(
            record_id=payment.id,
            category=diagnosis.category.value,
            promise_status=promise.status.value,
            gate_decision=decision.value,
            gate_reason=reason,
            idempotency_key=idempotency_key,
            is_recovered=is_rec,
            recovered_amount=rec_amt,
            ground_truth_status=payment.ground_truth_status.value,
        ))

    return results


def compute_metrics(batch: List[FailedPayment], results: List[RunResult]) -> dict:
    total = len(results)
    total_at_risk = sum(p.amount for p in batch)

    committed = [r for r in results if r.promise_status == PromiseStatus.COMMITTED.value]
    retried = [r for r in results if r.gate_decision == GateDecision.RETRY_SCHEDULED.value]
    stopped = [r for r in results if r.gate_decision == GateDecision.STOPPED.value]
    rejected = [r for r in results if r.gate_decision == GateDecision.REJECTED_INVALID.value]
    escalated = [r for r in results if r.gate_decision == GateDecision.ESCALATED_HUMAN.value]

    # --- 1. Commitment Detection Quality vs Hidden Ground Truth ---
    tp = sum(1 for r in results if r.promise_status == PromiseStatus.COMMITTED.value and r.ground_truth_status == PromiseStatus.COMMITTED.value)
    fp = sum(1 for r in results if r.promise_status == PromiseStatus.COMMITTED.value and r.ground_truth_status != PromiseStatus.COMMITTED.value)
    fn = sum(1 for r in results if r.promise_status != PromiseStatus.COMMITTED.value and r.ground_truth_status == PromiseStatus.COMMITTED.value)
    tn = sum(1 for r in results if r.promise_status != PromiseStatus.COMMITTED.value and r.ground_truth_status != PromiseStatus.COMMITTED.value)

    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
    f1 = round((2 * precision * recall) / (precision + recall), 4) if (precision + recall) > 0 else 0.0

    false_commitment_rate = round(fp / max(1, (tp + fp)), 4)
    missed_commitment_rate = round(fn / max(1, (tp + fn)), 4)

    # Invalid commitments: model extracted COMMITTED, but PolicyGate rejected as syntactically/financially invalid
    invalid_commitments = [r for r in committed if r.gate_decision == GateDecision.REJECTED_INVALID.value]
    invalid_commitment_rate = round(len(invalid_commitments) / max(1, len(committed)), 4)

    # --- 2. Financial Recovery: Vaapas vs Naive Baseline ---
    vaapas_recovered_amount = round(sum(r.recovered_amount for r in results), 2)
    vaapas_recovery_rate = round(vaapas_recovered_amount / total_at_risk, 4) if total_at_risk > 0 else 0.0

    baseline_metrics = FixedRetryBaseline.run(batch)
    baseline_recovered_amount = baseline_metrics["baseline_recovered_amount"]
    baseline_recovery_rate = baseline_metrics["baseline_recovery_rate"]

    absolute_recovery_uplift = round(vaapas_recovery_rate - baseline_recovery_rate, 4)
    relative_recovery_uplift = round(
        (vaapas_recovery_rate - baseline_recovery_rate) / max(0.0001, baseline_recovery_rate), 4
    )

    # --- 3. Recovery Efficiency: Recovered Amount / Authorized Retry Amount ---
    scheduled_record_ids = {r.record_id for r in retried}
    vaapas_authorized_amount = round(sum(p.amount for p in batch if p.id in scheduled_record_ids), 2)
    vaapas_recovery_efficiency = round(
        vaapas_recovered_amount / max(0.01, vaapas_authorized_amount), 4
    )

    baseline_authorized_amount = total_at_risk
    baseline_recovery_efficiency = round(
        baseline_recovered_amount / max(0.01, baseline_authorized_amount), 4
    )

    return {
        "total_records": total,
        "total_at_risk": round(total_at_risk, 2),
        "promise_capture_rate": round(len(committed) / total, 3) if total else 0,
        "retry_scheduled_rate": round(len(retried) / total, 3) if total else 0,
        "commitment_precision": precision,
        "commitment_recall": recall,
        "commitment_f1": f1,
        "false_commitment_rate": false_commitment_rate,
        "missed_commitment_rate": missed_commitment_rate,
        "invalid_commitment_rate": invalid_commitment_rate,
        "baseline_recovered_amount": baseline_recovered_amount,
        "baseline_recovery_rate": baseline_recovery_rate,
        "baseline_authorized_amount": baseline_authorized_amount,
        "baseline_recovery_efficiency": baseline_recovery_efficiency,
        "vaapas_recovered_amount": vaapas_recovered_amount,
        "vaapas_recovery_rate": vaapas_recovery_rate,
        "vaapas_authorized_amount": vaapas_authorized_amount,
        "vaapas_recovery_efficiency": vaapas_recovery_efficiency,
        "absolute_recovery_uplift": absolute_recovery_uplift,
        "relative_recovery_uplift": relative_recovery_uplift,
        "stopped_count": len(stopped),
        "escalated_to_human_count": len(escalated),
        "rejected_invalid_count": len(rejected),
    }


def run_benchmark(n: int = 55, seed: int = 42, log_path: str = "audit_log.jsonl") -> dict:
    batch = generate_batch(n=n, seed=seed)
    log = AuditLog(path=log_path)
    results = run_pipeline(batch, log)
    metrics = compute_metrics(batch, results)

    print(f"\nRan {len(batch)} synthetic records through the pipeline (Seed: {seed}).\n")
    print("Category breakdown:")
    from collections import Counter
    for cat, count in Counter(r.category for r in results).items():
        print(f"  {cat:24s} {count}")

    print("\nGate decisions (Deterministic Authority):")
    for dec, count in Counter(r.gate_decision for r in results).items():
        print(f"  {dec:24s} {count}")

    print("\nSimulation Performance vs Naive Baseline:")
    print(f"  Total GMV at Risk          : Rs {metrics['total_at_risk']:,.2f}")
    print(f"  Baseline Recovered         : Rs {metrics['baseline_recovered_amount']:,.2f} ({metrics['baseline_recovery_rate']*100:.2f}%)")
    print(f"  Vaapas Recovered           : Rs {metrics['vaapas_recovered_amount']:,.2f} ({metrics['vaapas_recovery_rate']*100:.2f}%)")
    print(f"  Absolute Recovery Uplift   : +{metrics['absolute_recovery_uplift']*100:.2f}%")
    print(f"  Relative Recovery Uplift   : +{metrics['relative_recovery_uplift']*100:.2f}%")
    print(f"  Vaapas Authorized GMV      : Rs {metrics['vaapas_authorized_amount']:,.2f}")
    print(f"  Vaapas Recovery Efficiency : {metrics['vaapas_recovery_efficiency']*100:.2f}% (recovered / authorized)")
    print(f"  Baseline Efficiency        : {metrics['baseline_recovery_efficiency']*100:.2f}% (recovered / attempted)")

    print("\nCommitment Detection Quality:")
    print(f"  Promise Capture Rate       : {metrics['promise_capture_rate']}")
    print(f"  Commitment Precision       : {metrics['commitment_precision']}")
    print(f"  Commitment Recall          : {metrics['commitment_recall']}")
    print(f"  Commitment F1 Score        : {metrics['commitment_f1']}")
    print(f"  False Commitment Rate      : {metrics['false_commitment_rate']}")
    print(f"  Invalid Commitment Rate    : {metrics['invalid_commitment_rate']}")

    print(f"\nAudit log written to {log.path} ({log.count()} entries)")
    return metrics


if __name__ == "__main__":
    run_benchmark(n=55, seed=42)
