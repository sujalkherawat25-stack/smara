# Load test: how many shops can ask at once

Harness: `evals/load_test.py` (real model or a fake one with `--llm fake --latency`). It needs a scratch database whose
name ends in `_eval` and drives the real Telegram handler, agent loop, scheduler and digests.

```
python -m evals.load_test --db <url ending _eval> --scenario burst --shops 20 --llm real
python -m evals.load_test --db <url> --scenario steady --shops 30 --per-shop 2 --window 90 --llm real
python -m evals.load_test --db <url> --scenario morning --shops 30 --due 3 --llm real
```

## What limits us

The model provider (one Sarvam key): about 60 to 100 calls a minute sustained, a burst of about 120, shared by both models.
A question to Smara costs about 4 to 5 calls (about 30k tokens). So one key serves about 12 to 20 questions a minute.
Everything else (database, event loop) was far from a limit: loop lag under 0.1 s, no errors.

## Results on the real model (one key)

| Scenario | Before | After |
|---|---|---|
| 10 shops ask at once | median 33 s, slowest 56 s | median 5 s, slowest 10 s |
| 20 shops at once | not measured | median 29 s, slowest 40 s |
| 30 shops at once | median 42 s, slowest 67 s, 369 refusals | median 58 s, slowest 79 s, 0 refusals |
| 30 shops, 2 questions each over 90 s | not measured | median 6 s, slowest 79 s |
| Morning: 30 digests + 90 due reminders | tick blocked on a queue of model calls | tick 11 s, 50 cards sent, 6 drafted by the agent |

All messages were answered in every run. At 30 shops asking in the same second the last one waits about 80 s: that is
the provider quota, not the server. Levers: more API keys (raise `LLM_AGENT_RPM` and `LLM_AGENT_BURST` about N times),
fewer calls per question.

## What changed

- Calls are paced to the provider's allowance (`LLM_AGENT_RPM`, `LLM_AGENT_BURST`) instead of being sent and refused.
- At most N calls run at once, cut by 30% on a refusal and given back slowly (`AdaptiveSlots`).
- A single key no longer freezes for 20 s after one refusal; it follows the provider's `Retry-After`.
- The spare model is not used when the provider is busy (it shares the quota): `LLM_FALLBACK_ON_BUSY=0`.
- Scheduler: due reminders are handled in parallel; at most 6 per tick are drafted by the agent, the rest get the ready-made card.
- The ledger line in the prompt lists the top overdue customers, so common questions need one model call.
