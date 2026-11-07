-- A part-payment lowers what is owed without being a discount; same owner-approval path.
ALTER TABLE proposed_adjustments DROP CONSTRAINT proposed_adjustments_adjustment_type_check;
ALTER TABLE proposed_adjustments ADD CONSTRAINT proposed_adjustments_adjustment_type_check
    CHECK (adjustment_type IN ('DISCOUNT_CLAIM', 'PROMISE_DATE_CHANGE', 'UTR_PROOF', 'PARTIAL_PAYMENT'));
