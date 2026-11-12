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
| **a** (live now) | Conversation log in the prompt, `search_chat_history`, and a Continuum lookup on every message. Facts reach Continuum the way the app writes them (the per-turn memory pass) |
| **b** | Continuum only. No chat history, no search. Every turn of the history is written to Continuum (3 turns per write) |
| **c** | Conversation log and search only. No Continuum |

## Result (15 questions, 2 repeats each = 30 answers per setup)

| Setup | Correct | Known facts | Never said (does not invent) |
|---|---|---|---|
| a: log + search + Continuum | **93%** (28/30) | 92% (22/24) | 100% (6/6) |
| c: log + search only | 87% (26/30) | 83% (20/24) | 100% (6/6) |
| b: Continuum only | 67% (20/30) | 58% (14/24) | 100% (6/6) |

| Kind of question | a | b | c |
|---|---|---|---|
| Exact detail (address, number, amount) | 9/10 | 6/10 | 9/10 |
| Told on one channel, asked on the other | 4/4 | 2/4 | 4/4 |
| Look-alike decoy (another customer's cheque) | 2/2 | 1/2 | 2/2 |
| Standing rule | 2/2 | 1/2 | 1/2 |
| Today (3 hours ago) | 2/2 | 1/2 | 2/2 |
| 70 days ago | 2/2 | 2/2 | 2/2 |
| **A rule that was changed** | **1/2** | **1/2** | **0/2** |
| Never said | 6/6 | 6/6 | 6/6 |

## What it shows

1. **Use both stores.** The live setup beat either alone. Continuum alone lost exact details that the chat log kept
   (an address, a cheque number, the count in a dispute, an amount and date).
2. **Continuum earns its place on rules and preferences.** Without it (c), Smara answered "what time may reminders go out?"
   from its built-in quiet hours and never searched the chat; it also read "who looks after Tally?" as a request to open
   Tally. With the automatic Continuum lookup (a), the rule was already in front of it.
3. **A rule that was changed is the weak spot in every setup.** "No reminders to Sharma before Friday" was later changed
   to "before Wednesday". Setup a got it right once in two tries, b once in two, c never. When it is wrong, Smara states
   the old rule as current.
4. **Memory facts can come out wrong.** "Pay Kumar Steel Rs 1,85,000 by the 15th" was stored by the memory pass as
   "Kumar Steel has to pay Rs 1,85,000", the wrong direction, and Smara once repeated it.
5. **Nothing was invented.** All three setups said "I do not know" on every question about things never said.

## Limits of this result

- 15 questions on one invented history with one model. It is good at separating 93% from 67%; it cannot separate 93% from 87%.
- Everything was written to Continuum within minutes, so its own timestamps cannot show real ageing. Continuum's
  conversation-ingestion settings were not tuned for setup b; a different ingestion setup might do better.
- The evaluation's own scoring was wrong in its first run (two cases, fixed with tests that use the real answers). Read the replies
  in the JSON output, not only the table.

## What to do about it

1. **Changed rules:** make a new rule replace the old one when it is written, instead of relying on the model to pick the
   last of two (Continuum has versioned slots; its cards currently say "unversioned").
2. **Direction and detail:** have the memory pass keep who-pays-whom and store the owner's own words with each fact.
3. **Rule-like questions:** always check memory for questions about rules, times and preferences, even when the agent
   believes it already knows.
4. Re-run this evaluation after each change, and add a real shop's conversations (with the owner's permission) once there is one.
