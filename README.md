# Vaapas (वापस) — Hinglish Payment & Mandate Recovery Agent

> **Vaapas is a safety-first AI revenue recovery agent for Indian payment failures that converts customer intent into bounded, measurable recovery actions.**

---

> [!NOTE]
> **Simulation Disclaimer**: All payment outcomes, customer transcripts, and recovery figures in this repository are synthetic simulation results produced by deterministic rule-based benchmark suites. They are not production Razorpay data and do not reflect live bank performance.

---

## Problem

Most payment recovery in India is English-first and one-shot: an automated SMS with a generic link, sent on a fixed schedule, to a customer who thinks and negotiates in Hinglish. It does not ask *why* the debit failed, nor *when* the customer will actually have the funds. Naive retries land on days salaries have not cleared, burning bank retry limits and leaving static links unread.

## Solution

Vaapas closes that loop by converting silent payment degradation into a structured, bounded recovery workflow:
1. **Diagnoses Failure Root Cause**: Classifies failures into transient infrastructure bottlenecks, liquidity timing, mandate lifecycle expirations, or checkout drop-offs.
2. **Hinglish Promise-to-Pay**: For cases where human timing matters, it conducts an authentic dialogue to capture an explicit repayment commitment.
3. **Deterministic Financial Safety Gate**: Zero LLM calls are permitted in financial authorization. A pure-Python Finite State Machine (FSM) enforces regulatory cooldowns, retry limits, discount caps, debt ceilings, and stopping rules before any retry can be scheduled.

## Architecture

```text
Synthetic failure  -->  RCA engine (Advisor)  -->  Negotiation agent (Advisor)
                                                          |
                                                          v
                                            Policy gate (Deterministic FSM)
                                            |-- retry caps (max 3)
                                            |-- cooldown enforcement (>= 4h)
                                            |-- discount ceiling (<= 10%)
                                            |-- debt bounds (<= outstanding)
                                            +-- stop & fraud conditions
                                                          |
                                     +--------------------+--------------------+
                                     v                    v                    v
                           retry_scheduled        escalated_human           stopped
                                     |                    |                    |
                                     +----------> audit_log.jsonl <------------+
                                             (written BEFORE execution)
                                                          |
                                                          v
                                            Idempotent Execution Engine
```

The system strictly enforces an **Advisor vs. Authority** boundary:
- **RCA Engine**: Advisor (identifies failure category).
- **Negotiation Agent**: Advisor (conducts dialogue in Hinglish).
- **Extraction Pass**: Structured Interpreter (extracts `{status, date, amount, confidence}`).
- **PolicyGate**: Sole Financial Authority (pure Python, zero LLM calls).
- **Audit Logger**: Append-only evidence committed to disk *before* downstream execution.

## Differentiation from Generic Retry Systems

```text
Naive fixed-retry:
FAILED  -->  WAIT 24 HOURS  -->  BLIND RETRY

Vaapas:
FAILED  -->  DIAGNOSE WHY  -->  TALK TO CUSTOMER  -->  CAPTURE WHEN THEY CAN PAY
        -->  VALIDATE COMMITMENT  -->  CHECK SAFETY POLICY  -->  RETRY ONLY WHEN AUTHORIZED
        -->  MEASURE ACTUAL RECOVERY
```

The core innovation is **not** "an LLM sends a message."  
It is that **LLM-derived intent is converted into a bounded financial action through a deterministic authorization layer.**

## Why Hinglish Negotiation Matters

Urban Indian customers rarely specify ISO dates during recovery outreach. When an autopay debit bounces, customers explain context in Hinglish:
> *"Bhaiya salary 10 tareek ko aayegi, tab account mein balance rahega, tab debit kar lena."*

A rigid automated SMS ignores this intent and blindly retries tomorrow, failing again. By engaging the customer in conversational Hinglish, Vaapas extracts a specific commitment date and aligns retry timing to the customer's actual liquidity window.

## Deterministic Safety Boundary

To prevent model hallucination or adversarial manipulation from triggering unauthorized financial actions:
- **Two-Stage Separation**: Negotiation dialogue and JSON extraction are isolated calls. This stops customer politeness (*"dekh lenge"* / we'll see) from being conflated with a binding promise.
- **Pure-Python FSM**: `policy_gate.py` has **zero LLM imports**. It independently verifies date syntax, positive amounts, debt ceilings, cooldowns, and opt-outs.
- **Zero Trust in Model Confidence**: Even if an upstream LLM claims `confidence = 0.999` on a fabricated or malicious promise, the PolicyGate rejects it if it violates financial invariants.
- **Idempotency**: Downstream execution enforces unique idempotency keys per attempt (`{record_id}_att{attempt}_{date}_{amount}`) to prevent duplicate debits.

## Benchmark Methodology

Every metric in this repository is computed from scratch by running the pipeline across synthetic transaction batches.

### Benchmark Run Log vs. Production Ledger
In this repository, `audit_log.jsonl` is initialized fresh per benchmark run so judges can inspect decisions from that run. In production, this logger writes to an append-only distributed ledger.

### Payment Outcome Simulation Assumptions
The benchmark uses `PaymentOutcomeSimulator` to evaluate whether an authorized retry recovers money. It receives **only** information available at execution time (no hidden ground truth). Its documented simulation rules:
- **Bank Timeouts**: High recovery probability (0.72) when retried after infrastructure stabilization cooldown.
- **Insufficient Funds**: High recovery probability (0.70) if executed on/after the customer's promised salary date; low probability (0.15) if retried prematurely; baseline probability (0.20) if untimed.
- **Mandate Expirations / Fraud / Opt-Outs**: Zero recovery (requires human/portal re-authorization).
- **Prior Attempt Penalty**: -0.07 per prior failed attempt.

## Baseline: FixedRetryBaseline

We evaluate Vaapas against a naive fixed-retry baseline (`FixedRetryBaseline`):
- Operates on the **exact same synthetic population**.
- Evaluated with the **exact same payment outcome simulator**.
- Naively retries after a fixed 24-hour delay without customer conversation.

## Results

### Official Run: N=55 Records (Seed: 42)
Command: `python main.py`

```text
Simulation Performance vs Naive Baseline:
  Total GMV at Risk          : Rs 390,109.52
  Baseline Recovered         : Rs 58,383.90 (14.97%)
  Vaapas Recovered           : Rs 73,138.30 (18.75%)
  Absolute Recovery Uplift   : +3.78%
  Relative Recovery Uplift   : +25.25%
  Vaapas Authorized GMV      : Rs 181,889.45
  Vaapas Recovery Efficiency : 40.21% (recovered / authorized)
  Baseline Efficiency        : 14.97% (recovered / attempted)

Commitment Detection Quality (vs Hidden Ground Truth):
  Promise Capture Rate       : 0.491  (27 of 55 produced commitments)
  Commitment Precision       : 1.000  (Zero false positive commitments)
  Commitment Recall          : 1.000  (All valid commitments detected)
  Commitment F1 Score        : 1.000
  False Commitment Rate      : 0.000
  Invalid Commitment Rate    : 0.000

Gate Decisions:
  retry_scheduled            : 27
  escalated_human            : 19
  stopped                    : 9
```

*Note on 1.000 F1 Score*: In the deterministic synthetic benchmark population, the prompt-isolated extractor cleanly identifies clear commitments from non-commitments. In production with noisy audio/text, precision and recall will vary.

### Multi-Seed Benchmark: N=500 Records (5 Seeds)
Across seeds `[42, 43, 44, 45, 46]` on 500 records each:
- **Baseline Recovery Rate**: mean = 19.79% (std = 0.56%)
- **Vaapas Recovery Rate**: mean = 21.64% (std = 3.51%)
- **Absolute Recovery Uplift**: mean = +1.84% (std = 3.12%)
- **Relative Recovery Uplift**: mean = +9.08% (std = 15.65%)
- **Commitment F1 Score**: 1.000

*Honest evaluation note*: In populations with high refusal or chronic mandate expiration rates, uplift is modest or slightly negative due to selective gating; in liquidity-heavy populations, timing promises deliver significant recovery uplift (+21% to +27%).

## Safety Evaluation

The repository includes a comprehensive 25-test suite (`tests/test_policy_gate.py`) proving:
1. **Hard Stops**: Explicit "STOP" in customer text, merchant opt-out flags, and fraud indicators immediately halt recovery.
2. **Retry Cap**: Cannot exceed 3 attempts under any condition.
3. **Cooldown**: Minimum 4-hour cooldown strictly enforced.
4. **Boundary Checks**: Missing dates, malformed ISO, negative amounts, zero amounts, NaN, Infinity, and amounts exceeding debt owed are rejected as `REJECTED_INVALID`.
5. **Idempotency**: Duplicate executions of the same idempotency key are rejected downstream.
6. **Ground Truth Isolation**: Hidden benchmark ground truth is isolated and never passed into inference.
7. **Adversarial Resilience**: An injected promise for Rs 99,999,999 with 0.999 reported confidence is blocked by the gate.

## Demo

Run the interactive two-case demonstration:
```bash
python demo.py
```
- **Case A**: Demonstrates end-to-end Hinglish negotiation for a delayed salary, validation by the gate, and successful timed debit.
- **Case B**: Demonstrates an adversarial prompt injection with 0.999 reported confidence rejected by the gate.

## Running Locally

### 1. Requirements
Python 3.10+ and `pytest`:
```bash
pip install -r requirements.txt
```

### 2. Run Test Suite (25 tests)
```bash
python -m pytest tests/ -v
```

### 3. Run Benchmark
```bash
# Default (N=55, seed 42)
python main.py

# Large scale (N=500, seed 42)
python main.py --size 500 --seed 42
```

### 4. Run Interactive Demo
```bash
python demo.py
```

## Live Claude Mode

By default, Vaapas runs in zero-dependency **Simulator Mode** using a deterministic stand-in customer and extractor (stable SHA-256 seed).

To test with live Claude:
1. Set your Anthropic API key in `.env`:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-...
   ```
2. Run `python main.py`. Both `negotiate()` and `extract()` will execute live API calls via `claude-3-5-sonnet-20241022`. If an API error or network failure occurs, it falls back to a safe refusal/escalation so no unverified financial action is ever taken.

## Limitations

- **Synthetic Outcomes**: Outcomes are modeled using domain heuristics; live bank networks experience fluctuating latency and switch downtimes.
- **Single Turn**: The current negotiation agent executes a single turn exchange; complex multi-turn disputes require human escalation.
- **Text-Only**: Hinglish outreach is evaluated via text/SMS/chat; voice synthesis and ASR are not modeled.

## Future Production Integration

1. **Razorpay Webhook Listener**: Replace synthetic batch generation with FastAPI endpoints consuming `payment.failed` and `subscription.halted` webhooks.
2. **Razorpay Optimizer / Payment Links API**: Connect `RETRY_SCHEDULED` actions to dynamic Razorpay Payment Links dispatched via WhatsApp Business API.
3. **Distributed Lock & Ledger**: Back `PolicyGate._state` with Redis/PostgreSQL row locks and append-only cryptographic event stores.
