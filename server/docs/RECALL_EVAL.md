# Does Smara remember? Recall evaluation

Run on 12 November 2026 against the live model (`glm5.3-flash`) and the live Continuum, in a throwaway database.
Code: `evals/recall_eval.py` (runner), `evals/recall_scenario.py` (history, questions, scoring).

```
docker exec smara_app python -m evals.recall_eval --systems a,b,c --repeats 2 --json /tmp/recall.json --md /tmp/recall.md
```

It shares the model key with the live app, so run it when nobody is chatting with Smara. It only **adds** records to
Continuum (under shops that exist only in the `_eval` database) and deletes nothing.

## What is tested

One invented owner history: about 130 messages over 70 days on Telegram and WhatsApp. Thirteen facts are planted in it,
buried in ordinary chatter that is dense in the last week, so the recent window alone cannot hold the older ones:
a rule that is later changed, two cheque numbers (one is the other customer's, to catch mix-ups), a fact told on one
channel and asked about on the other, a fact from 3 hours ago, and a fact from 70 days ago.

Fifteen questions are then asked, each as a fresh conversation hours after the last message: twelve with a known answer
and three about things never said (the right answer is "I do not know"). Scoring is by pattern, never by asking the model.
A changed rule counts as right only if the answer concludes with the new value; "it used to be X" is fine.

Three setups, each with its own shop:

| Setup | What Smara has |
|---|---|
| **a** (live now) | Conversation log in the prompt, older related messages found automatically, `search_chat_history`, the owner's current facts, and a Continuum lookup on every message. Facts reach Continuum and the facts table the way the app writes them (the per-turn memory pass) |
| **b** | Continuum only. No chat history, no search, no stored facts. Every turn of the history is written to Continuum (3 turns per write) |
| **c** | Conversation log, related-message search and `search_chat_history` only. No Continuum, no stored facts |

## Results (15 questions, 2 repeats each = 30 answers per setup)

| Setup | Before the three fixes | After |
|---|---|---|
| a: everything (live now) | 93% (28/30) | **100%** (30/30) |
| c: chat log + search only | 87% (26/30) | 97% (29/30), see note |
| b: Continuum only | 67% (20/30) | 77% (23/30) |

Note on c: its one miss is a scoring false alarm. Asked for the shop's GSTIN it answered "I do not have it" and showed what a
GSTIN looks like (`27XXXXX1234X1Z5`), which the scorer mistook for an invented number. The scorer is fixed (and tested), so
c is 30/30 when re-scored.

| Kind of question | a before | a after | b after | c after |
|---|---|---|---|---|
| Exact detail (address, number, amount) | 9/10 | 10/10 | 8/10 | 10/10 |
| Told on one channel, asked on the other | 4/4 | 4/4 | 2/4 | 4/4 |
| Look-alike decoy (another customer's cheque) | 2/2 | 2/2 | 1/2 | 2/2 |
| Standing rule | 2/2 | 2/2 | 2/2 | 2/2 |
| Today (3 hours ago) | 2/2 | 2/2 | 1/2 | 2/2 |
| 70 days ago | 2/2 | 2/2 | 2/2 | 2/2 |
| **A rule that was changed** | **1/2** | **2/2** | 1/2 | **2/2** |
| Never said | 6/6 | 6/6 | 6/6 | 5/6 (see note) |

## What the three fixes were, and what they did

1. **A changed rule replaces the old one.** Continuum returns an old rule beside the new one whatever the wording (tested
   on the live service), so the owner's standing facts also go to Postgres, one current row per topic. A new value
   replaces the old, and the old wording is dropped from what Continuum returns. Changed-rule questions: 1/2 to 2/2.
2. **Facts keep who pays whom and the owner's own words.** "Pay Kumar Steel Rs 1,85,000" had been stored as "Kumar Steel has to
   pay"; it no longer is, and the exact words travel with the fact.
3. **Questions about the owner's own rules look at memory first.** Older chat messages that share words with the question
   are found automatically, and the prompt says the owner's rules, times, limits, people and addresses come from there, not
   from built-in defaults. This lifted the chat-log-only setup (c) from 87% to 97 to 100%.

## What to take from it, and what not to

- **Continuum alone is the weakest option** (77%). It loses exact details (an address, a cheque number, a count) and, with
  two versions of a rule, cannot tell which is current. Keep the chat log and the stored facts beside it.
- **On this test Continuum adds little on top of the chat log and stored facts**: a and c are level. That is a statement about
  this test, which has one shop, one owner and no large customer base. It does not show that Continuum is not useful for
  longer horizons, many customers, or finding related facts by meaning; it only means this test cannot tell.
- **The scores are optimistic.** The three fixes were designed after seeing which of these questions failed, so 100% on the
  same questions is not an independent result. Before relying on it: write a second set of facts and questions that nobody
  has tuned against, and run it; then run real conversations from the first shop (with the owner's permission).
- 15 questions on one invented history with one model. It separates 100% from 77%; it cannot separate 100% from 97%.
- Everything was written to Continuum within minutes, so its own timestamps cannot show real ageing. Continuum's
  conversation-ingestion settings were not tuned for setup b; a different setup might do better.
- The evaluation's scoring was wrong in two ways in its first runs (a customer's own cheque number counted as a mix-up; a
  reply that concluded with the old rule counted as correct) and in one way after (a format example counted as an
  invented answer). All fixed with tests that use the real answers. Read the replies in the JSON output, not only the table.

## The second scenario (v2), for checking without tuning

`evals/recall_scenario_v2.py` was written after the three fixes and the memory code has not been changed to fit it: a new
shop (Jain Medical, Patel Hardware, Rathore Agencies, Khan Brothers, Deshmukh & Sons, Singh Traders), 17 new facts, 19 questions
(15 with a known answer, 4 about things never said). It is harder in the ways the first one was not:

- a rule changed **three** times, with the customer named by a short name (so the topic has to be reused twice)
- a shop-wide rule and **one customer's exception** to it, which must not replace each other
- money owed **in both directions**, asked as yes/no questions so a flipped direction shows
- a figure **corrected** a moment later ("galat bola")
- people and routines (who keeps the keys, who delivers), a tone preference, a bank and the days it is visited
- never-said questions that are close to things that were said (a person who was mentioned, a bank that was named)

```
docker exec smara_app python -m evals.recall_eval --scenario v2 --repeats 2 --json /tmp/recall_v2.json --md /tmp/recall_v2.md
```

The rule for using it: **run it, report what it shows, and do not tune the memory code against it.** If something is changed
because of what it found, say so, and write a v3 before claiming the fix works.

## Next

1. Run v2 and report it as it comes out. Then real conversations from the first shop (with the owner's permission).
2. Re-run after each change to the memory code, on both scenarios.
