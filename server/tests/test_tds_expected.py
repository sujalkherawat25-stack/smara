"""mh.tds_expected: the TDS each payment should have had; threshold first, no PAN raises the rate only where TDS applies, half-up rupees."""
import io
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import smara_helpers as mh  # noqa: E402

from evals import ca_data as cd  # noqa: E402

RATES = {"194C": {"Individual": 1, "Company": 2}, "194J": 10, "194I": 10, "194H": 2}


def test_it_reproduces_the_independent_key_on_the_planted_payments():
    f, t = cd.tds()
    df = pd.read_excel(io.BytesIO(f["vendor_payments_tds.xlsx"]), header=3)
    df = df.rename(columns={"Gross Amount": "Amount"})
    want = mh.tds_expected(df, rates=RATES, amount="Amount", section="Section", pan="PAN", vendor_type="Vendor Type", thresholds={"194C": 30000})
    assert float(want.sum()) == t["expected_total"]
    short = df[(want - df["TDS Deducted"]) > 1]
    assert len(short) == t["short_n"] and float((want - df["TDS Deducted"])[short.index].sum()) == t["short_total"]
    assert int(((want - df["TDS Deducted"]) < -1).sum()) == t["excess_n"]


def test_a_payment_below_the_threshold_has_no_tds_even_without_a_pan_and_halves_round_up():
    df = pd.DataFrame({"Section": ["194C", "194C", "194H", "194J"], "Vendor Type": ["Company", "Company", "Individual", "Individual"],
                       "PAN": [None, None, "AAAPA1111A", "NA"], "Amount": [30000, 30001, 4125, 10000]})
    got = list(mh.tds_expected(df, rates=RATES, vendor_type="Vendor Type", thresholds={"194C": 30000}))
    assert got == [0.0, 6000.0, 83.0, 2000.0]            # 30,000 is at the threshold; no PAN: 20% not 2%; 4125 x 2% = 82.5 -> 83; no PAN on 194J: 20%
