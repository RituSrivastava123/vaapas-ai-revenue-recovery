# Razorpay Buildathon — Form Answers

**Selected Track**
Track 3: AI Revenue Recovery

**Project Name / Title**
Vaapas — a Hinglish agent that gets failed payments paid back

**Project Objectives**
Most payment recovery in India is English-first and one-shot: an SMS with a link, sent on a fixed schedule, to a customer who thinks and negotiates in Hinglish. It doesn't ask why the payment failed, and it doesn't ask when the customer will actually have the money — so retries land on days salaries haven't cleared, and links go unread.

Vaapas closes that loop. It diagnoses why a payment failed (bank timeout, insufficient funds, expired mandate, or drop-off at OTP), and for the cases a conversation can fix, it negotiates a specific repayment promise in Hinglish instead of sending a generic reminder — then schedules the retry for that exact moment instead of guessing.

The negotiation never touches money directly. A deterministic gate sits between the agent and any action it proposes: retry caps, cooldown windows, a hard discount ceiling, and stop rules that fire on a customer saying "stop" or on repeated bank rejections. Every decision the gate makes is written to an audit log before it executes, not after.

Measured against a naive fixed-retry baseline on identical synthetic populations: Vaapas achieves an absolute recovery uplift of +3.78% (relative uplift +25.25%) on a 55-record batch, and a mean relative uplift of +9.08% across 5 seeds (N=500). In commitment detection against hidden ground truth, the two-stage extractor achieves 1.000 Precision, 1.000 Recall, and 0.000 false commitments, while the deterministic gate safely escalates unconfirmed cases to human review.

**Build Challenges & Technical Obstacles**
The negotiation and the extraction had to be two separate calls, not one. Early on, a single call that both talked to the customer and decided whether they'd committed kept mislabeling politeness as commitment — "dekh lenge" (we'll see) isn't the same as a date and an amount. Splitting negotiation from a strict-schema extraction pass, with an explicit "acknowledged" vs "committed" distinction, fixed it: only a committed promise can schedule a retry, everything else goes to a human queue.

The retry scheduler needed to be idempotent. State is tracked per record so cooldowns and retry caps hold across the whole batch, not just within a single call — a dropped call or a retried webhook can't double-schedule.

The hardest constraint was keeping the LLM strictly advisory. It was tempting to let the negotiation step decide retry timing directly since it already had the promised date — but that puts an unbounded system in charge of money movement. Instead the negotiation agent only proposes; a plain-Python finite state machine with zero model calls enforces the actual limits (retry caps, cooldowns, a 10% discount ceiling, stop conditions), and it's the only thing with permission to trigger an action. We tested this adversarially: feeding the gate a fabricated "committed" promise for an amount far beyond what was owed, reported with 0.99 confidence — the gate rejected it anyway, because it checks the actual numbers against the record, not the model's stated confidence. See `tests/test_policy_gate.py::test_21_adversarial_confidence_099_rejected`.

**GitHub Repository URL**
[fill in after pushing]

**5-min Pitch Video Link**
[fill in after recording]
