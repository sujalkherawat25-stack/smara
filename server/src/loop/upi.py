from urllib.parse import quote


def rupees(paise: int) -> str:
    whole, frac = divmod(paise, 100)
    return f"₹{whole:,}" + (f".{frac:02d}" if frac else "")


def upi_link(vpa: str, payee: str, amount_paise: int, note: str) -> str:
    """upi://pay deep link. Only ever dispatched after owner approval."""
    return (f"upi://pay?pa={quote(vpa, safe='@')}&pn={quote(payee)}"
            f"&am={amount_paise / 100:.2f}&cu=INR&tn={quote(note)}")


def upi_text(vpa: str, amount_paise: int, ref: str) -> str:
    """Plain, copyable payment instruction (WhatsApp does not linkify upi://)."""
    return f"Pay {rupees(amount_paise)} to {vpa} (UPI). Ref: {ref}"


def pay_url(base_url: str, vpa: str, payee: str, amount_paise: int, note: str) -> str:
    """Tappable https link that /pay redirects to the UPI app."""
    return (f"{base_url.rstrip('/')}/pay?pa={quote(vpa, safe='@')}&pn={quote(payee)}"
            f"&am={amount_paise / 100:.2f}&tn={quote(note)}")
