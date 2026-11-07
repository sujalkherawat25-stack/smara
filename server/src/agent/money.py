"""Indian rupee formatting (lakh / crore grouping): 12345678.5 -> ₹1,23,45,678.50."""
from decimal import ROUND_HALF_UP, Decimal


def inr(value, decimals: int = 2, symbol: bool = True) -> str:
    q = Decimal(str(value or 0)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    whole, _, frac = f"{abs(q):f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    body = ",".join(groups + [tail]) if groups else tail
    return f"{sign}{'₹' if symbol else ''}{body}{('.' + frac) if decimals else ''}"
