# Smara demo script (6 to 7 minutes, on a phone)

**The promise in one line:** send Smara the file you already have and talk to it in Hinglish. It finds who owes you, chases them with your
approval, keeps your udhaar book by chat, matches your GST, and sends the PDFs. No new software to learn.

Everything below was rehearsed as one conversation on the live server with the real model: 3 runs out of 3 passed every check, with
188 to 230 seconds of Smara working time over the 11 steps (the rest of the 6 to 7 minutes is you talking). The replies here are from
those runs. Wording varies a little each time; the figures do not.

## Before you start (15 minutes, the morning of the demo)

1. **Make the files.** From the `server/` folder: `python docs/demo/make_demo_files.py demo_files`. Dates are relative to today, so the
   figures below hold on any day. It writes three files: `1_Outstanding_Receivables.xlsx`, `2_Purchase_Register_Oct.xlsx`, `3_GSTR2B_Oct.xlsx`.
   Put them where you can send them from the phone (Telegram "Saved Messages" or the phone's files).
2. **Use a fresh demo shop**, not a real owner's. Create an invite (`docker exec smara_app python scripts/invites.py create --label "Demo"`, see
   PILOT_RUNBOOK) and join from the demo phone. The ledger must be empty at the start, or the totals will not match.
3. **Check the server:** `https://ai.syntarus.com/smara/healthz` shows `"ok":true`. Do one dry run of step 1 an hour before.
4. **Record a fallback video** of a good run (3 of the 11 steps take 30 to 45 seconds; a video saves you if the model is slow or the network drops).
5. Know that **reminders go out from the owner's own WhatsApp** for now: Smara writes the message, the owner taps a link and sends it from
   their own number (that mode is on in production, and the official Smara number is still waiting on Meta). This is a selling point,
   not a gap: nothing reaches a customer without the owner's tap.

## The run

Say what you are about to do, send it, and use the waiting seconds to talk. Figures are for the generated files.

| # | You send | Smara does (typical time) | What to say |
| --- | --- | --- | --- |
| 1 | The Tally export `1_Outstanding_Receivables.xlsx`, caption "Ye mera outstanding hai." | Imports it in one go and replies: **10 invoices, ₹4,53,225, ₹4,28,125 overdue (7 invoices)**, the three biggest (Agarwal ₹2,20,000, Singh ₹98,750, Chopra ₹42,000), and that Chopra Sons and Dutta Agencies have no WhatsApp number. (5 to 8 s) | "This is the file you already export from Tally. No setup, no typing. It checked its own total against the sheet." |
| 2 | "Aaj kisko call karun? Sabse zyada overdue wale top 3 batao, amount ke saath." | The three biggest with days late, ₹3,60,750 together (84% of what is overdue), and a note that Chopra has no number so a call is the way. (17 to 31 s) | "It tells you who to call today, in your language." |
| 3 | "Chopra Sons 9811144444, Dutta Agencies 98222 55555" | Saves both numbers and says exactly which customer got which number. (10 s) | "You just type the numbers. Fixing a sheet is a chat message." |
| 4 | "Chopra Sons ko payment ka reminder bhejo, UPI ke saath." | Shows a **card**: the exact message that would go, with the invoice, ₹42,000 and your UPI ID. You tap **Approve**; it then sends you a link that opens WhatsApp with the message ready to send from your own number. (30 to 42 s, the slowest step) | "Nothing goes to a customer without your tap, and you see the exact words. While it writes, explain the approval rule." |
| 5 | "Ravi Kirana Store ko aaj 12000 ka maal udhaar diya, 15 din mein dega." | A card: Ravi Kirana Store owes ₹12,000, new invoice number, due in 15 days. Tap Approve: "Record ho gaya". (12 s) | "This is your udhaar book. You speak, it writes, you approve." |
| 6 | "Gupta Traders ne 5000 cash diya." | A card: ₹5,000 received, applied to their oldest invoice (B-125: ₹7,000 left), Gupta still owes ₹14,800. Tap Approve. (12 s) | "No invoice number needed. It splits the money across the oldest bills for you and works out the balance, not the AI's guess." |
| 7 | "Gupta Traders ka ab kitna baaki hai?" | **₹14,800**, with B-125 ₹7,000 and B-131 ₹7,800. (12 s) | "The books really changed. Ask again, same answer." |
| 8 | The purchase register `2_Purchase_Register_Oct.xlsx`, caption "Ye mera purchase register hai." | It does **not** import it as customer dues; it says it is a purchase register and asks for the GSTR-2B. (12 to 44 s) | "It knows a purchase file is not money owed to you. It will not mix them up." |
| 9 | `3_GSTR2B_Oct.xlsx`, caption "Ye GSTR-2B hai. Dono match karo: kitna ITC miss hai aur kis supplier ko chase karun?" | A PDF report plus the answer: 2 of 4 matched, **ITC at risk ₹900**, chase **Kumar Steel** (invoice K-4 missing from 2B) and **Delhi Pipes** (D-7: ITC ₹900 in books, ₹450 in 2B); Shah Fasteners' invoice is in 2B but not in your books. (17 to 24 s) | "This is the GST check your CA does at month end. It took seconds. It only reads the files; it never logs into the GST portal." |
| 10 | "Gupta Traders ka statement PDF bana ke bhejo." | A one-page customer statement PDF, ₹14,800. (about 30 s) | "Send this to the customer as it is." |
| 11 | "Business ka summary do: kul kitna baaki hai aur kitna overdue?" | **₹4,60,225 total** (11 invoices), **₹4,23,125 overdue** (7), the top three, and today's ₹5,000 collection. It matches steps 1, 5 and 6: 4,53,225 + 12,000 − 5,000. (18 to 24 s) | "One picture of your money. The morning digest sends this to you every day by itself." |

**Close (30 seconds):** "One file, a few messages, and in under four minutes of the machine's time it imported your dues, told you who to call, kept
your books, caught ₹900 of tax credit and made your PDFs. It never touched a customer or your bank. What would that take you today?"
Then ask the owner how long their own month-end takes. Do not quote a number of hours saved: it is the owner's number, not ours.

## What not to claim, and how to answer

| They ask or you are tempted to say | Say this instead |
| --- | --- |
| "Does it do my expenses and stock?" | Not yet. It keeps what customers owe you and what you owe suppliers. It says so plainly if you ask it for expenses or stock. |
| "Can I send voice notes?" | Voice notes are built but we have not tested them with the real model yet. Do not demo them. |
| "Can it read a photo of my khata page?" | Not tested. Do not demo it. |
| "Does it message my customers by itself?" | No. It writes the message, you approve it, and for now you send it from your own WhatsApp. The official number comes with Meta approval. |
| "Does it connect to Tally?" | Yes, the Smara Desktop helper reads TallyPrime each night. Today's demo uses the file you export, which needs nothing installed. |
| "Supplier bills (what I owe)?" | They come from the Tally sync. A shop without Tally cannot send them by chat yet. |
| "Is my data safe?" | Your ledger is kept per shop. It never moves money or holds bank logins. Customers' messages cannot change your books: only your tap does. |
| "What does it cost?" | Not decided. It is free during the early beta. Do not quote a price. |

## If something goes wrong

- **A step is slow (30 to 45 s):** keep talking about the approval rule or the next step; the answer arrives. If it passes a minute, say "let me show you a run I did earlier" and play the video.
- **Step 1 replies with a question instead of importing:** answer "haan, import karo". It happens rarely now (it was 11 of 20 before the fix; 20 of 20 since).
- **The numbers differ from this page:** the ledger was not empty. Use a fresh shop and re-run step 1 on a new one.
- **A card does not appear:** send the message again. Do not tap an old card.
- **Ravi Kirana's reply mentions a "promise for INV-101 on 19 Nov":** that line comes from the rehearsal's fake memory, not the real system. In a live demo it will not be there.
- **Never improvise the numbers:** if a figure looks wrong, say "let me check the ledger" and ask "Gupta Traders ka kitna baaki hai?".

## Re-running the rehearsal yourself

On the server, in a throwaway container (it uses a scratch database ending in `_eval`, a temp folder for files and the real sandbox; it
does not touch any shop):
`python -m evals.demo_rehearsal --db <the _eval database> --model glm5.3-flash --sandbox container --jobs /jobs`.
It prints this whole conversation with timings and ends with seven checks (10 invoices imported, sale recorded, numbers saved, cash applied
oldest-first, the new balance shown, both PDFs sent, purchase bills kept out of the receivables). Run it after any release and before
any demo. `tests/test_demo_files.py` checks the files against the real importer and GST code, so the figures here cannot drift.
