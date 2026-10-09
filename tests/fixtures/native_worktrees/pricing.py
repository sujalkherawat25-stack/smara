def discounted_total(subtotal, percent):
    """Apply a percentage discount; reject percentages outside 0..100."""
    if not 0 <= percent <= 100:
        raise ValueError("invalid discount")
    return subtotal * percent / 100
