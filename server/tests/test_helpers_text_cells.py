"""A customer or supplier name that begins with = must come out of mh.write_xlsx as text, never as a live formula."""
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import smara_helpers as mh  # noqa: E402


def test_write_xlsx_keeps_a_name_that_looks_like_a_formula_as_text(tmp_path):
    path = tmp_path / "o.xlsx"
    mh.write_xlsx({"Parties": pd.DataFrame({"Party": ['=HYPERLINK("http://evil.example","x")', "Anand Stores"], "Amount": [10, 20]})}, str(path))
    ws = load_workbook(path).active
    assert [(c.value, c.data_type) for c in ws["A"]][1:] == [('=HYPERLINK("http://evil.example","x")', "s"), ("Anand Stores", "s")]
    assert [c.value for c in ws["B"]][1:] == [10, 20]                              # numbers are still numbers
