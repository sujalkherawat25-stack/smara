def shipping_cost(weight):
    """First kg costs 5; each started additional kg costs 2. Reject nonpositive weight."""
    if weight <= 0:
        raise ValueError("invalid weight")
    if weight <= 1:
        return 5
    return 5 + int(weight) * 2
