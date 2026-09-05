# 5-Minute Pitch Script

**0:00–0:30 — Problem**
"Failed payments in India get recovered by SMS, in English, on a fixed schedule. Customers negotiate in Hinglish and have specific days their salaries clear — recovery systems don't ask, they just guess and blindly resend, burning bank retry limits and customer trust."

**0:30–1:30 — Architecture & The Advisor/Gate Split**
Show the README diagram. Say it out loud:
"The RCA engine and the negotiation agent only ever *advise*. Look at `policy_gate.py` — it has zero LLM calls in it. It's a pure-Python finite state machine and the only component with permission to schedule a retry, enforce cooldowns, or halt contact.
Notice our two-stage separation: negotiation and extraction are isolated calls. That prevents politeness like *'dekh lenge'* from being mistaken for a binding promise."

**1:30–3:00 — Live Run & Audit Evidence**
Run `python demo.py` live on screen.
Show Case A:
- Failed payment (Rs 3,499)
- Customer Hinglish dialogue: *"Salary 10 ko aayegi, tab debit karna"*
- Extraction: `COMMITTED`
- PolicyGate verification: amount <= debt, valid ISO date, cooldown satisfied, no STOP
- Scheduled and recovered!
Then open `audit_log.jsonl`:
"Every decision is committed to disk before any downstream execution occurs."

**3:00–4:15 — The Adversarial Test (Zero-Trust Gate)**
Run `python -m pytest tests/ -v` live.
Show `test_21_adversarial_confidence_099_rejected`:
"I fed the gate a fabricated commitment for Rs 99 Million on a Rs 1,000 debt, reported with 99.9% model confidence. The gate rejected it anyway. Why? Because the PolicyGate checks the actual debt bounds against the record, completely ignoring model confidence.
Also show test 24: duplicate idempotency keys are blocked downstream, guaranteeing no double-debiting."

**4:15–5:00 — Real Evaluated Results**
Run `python main.py` on screen.
"We evaluated Vaapas against a standard naive fixed-retry baseline on the exact same synthetic population with the exact same outcome simulator.
Vaapas delivers an absolute recovery uplift of +3.78% (relative uplift +25.25%) on 55 records, and a mean relative uplift of +9.08% across 5 multi-seed batches. Against hidden ground truth, commitment extraction achieved 1.000 Precision with zero false commitments.
The core takeaway for judges: **The AI can recommend a financial action. It cannot authorize one.**"
