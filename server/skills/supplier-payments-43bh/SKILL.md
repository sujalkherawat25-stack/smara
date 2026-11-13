---
name: supplier-payments-43bh
description: Planning supplier payments around the Section 43B(h) deadline for micro and small suppliers, and classifying suppliers. Use when the owner asks what to pay this week, which supplier bills are risky, MSME payment rules, or when payables data arrives from Tally.
---

# Supplier payments and Section 43B(h)

## The rule in plain words
If a business buys goods or services from a **micro or small enterprise** and does not pay within the legal time, the amount is not allowed as a tax deduction in the year of purchase; it is allowed only in the year it is actually paid. The legal time is **15 days** from the day the goods or services were accepted, or up to **45 days** if there is a written agreement that says so. Medium and large suppliers are not covered. Interest to a micro/small supplier on late payment is a separate rule (MSMED Act, section 16: three times the bank rate, compounded monthly).

Important limits of what you know:
- The 15/45 days run from the day of **acceptance**, which is usually the delivery or invoice date. Smara only holds the **bill date**, so the deadline you compute is an estimate. Say so.
- Whether a supplier is micro or small depends on its **Udyam registration**. You cannot tell from a name. A supplier you do not know is `unknown`, and unknown means "no deadline computed", not "safe".
- Do not give tax advice beyond this. For the exact tax position tell the owner to confirm with their CA.

## Data
Table `payables` with columns supplier, supplier_gstin, bill_ref, bill_date, amount_rupees, msme_class, agreement_45, paid_at. Open bills have `paid_at IS NULL`. `msme_class` is micro, small, other or unknown. The deadline is `bill_date + 15` (or `+45` when `agreement_45`) for micro/small only.

## What to do when asked "what should I pay this week?"
1. `query_ledger_sql` the open payables; group into: past deadline, due within 7 days, due later, unclassified.
2. Answer in that order. For each past-deadline bill say how many days late. These are the ones where the deduction may already be at risk, so they come first; paying now still stops the problem growing and ends the interest.
3. Offer the schedule on paper: `make_document` kind `payables_schedule`, then `send_file_to_owner`.
4. Do not suggest delaying a payment to a micro/small supplier to improve cash flow without stating the 43B(h) cost.

## Classifying suppliers
- Ask the owner, one question at a time, for the unclassified ones with the largest amounts: "Is Kota Steel registered as a micro or small enterprise (Udyam certificate)?" A supplier's invoice often prints its Udyam number; if the owner sends a photo, use `read_attachment` and quote the number you read.
- Save the answer with `write_continuum_evidence` (party_id `shop`): "Kota Steel = small, written agreement 45 days: yes/no". The nightly Tally sync reads the class from the helper's configuration; tell the owner that the class is also set there if it keeps reverting.
- Never guess a class to fill a gap.

## Cash-flow honesty
When the owner is short of cash, help them choose: pay micro/small suppliers past their deadline first, then those about to cross it, then everything else by the supplier's own terms. Show the totals for each step so they can see what is possible.

## Mistakes to avoid
- Presenting an estimated deadline as the legal deadline.
- Treating `unknown` suppliers as exempt.
- Mixing paid and unpaid bills: only `paid_at IS NULL` counts.
