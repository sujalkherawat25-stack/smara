def clamp(value, lower, upper):
    """Keep value within the inclusive bounds; reject reversed bounds."""
    if lower > upper:
        raise ValueError("lower must not exceed upper")
    return min(lower, max(value, upper))
