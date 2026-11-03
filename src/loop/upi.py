from urllib.parse import quote


def rupees(paise: int) -> str:
    whole, frac = divmod(paise, 100)
    return f"₹{whole:,}" + (f".{frac:02d}" if frac else "")


def upi_link(vpa: str, payee: str, amount_paise: int, note: str) -> str:
    """upi://pay deep link. Only ever dispatched after owner approval."""
    return (f"upi://pay?pa={quote(vpa, safe='@')}&pn={quote(payee)}"
            f"&am={amount_paise / 100:.2f}&cu=INR&tn={quote(note)}")
