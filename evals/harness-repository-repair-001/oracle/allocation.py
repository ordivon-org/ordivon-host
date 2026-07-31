from __future__ import annotations


def allocate_units(total: int, weights: list[int]) -> list[int]:
    """Allocate integer units proportionally across positive integer weights."""
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("total must be a non-negative integer")
    if not isinstance(weights, list) or not weights:
        raise ValueError("weights must be a non-empty list")
    if any(
        isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0
        for weight in weights
    ):
        raise ValueError("weights must contain positive integers")

    weight_total = sum(weights)
    allocations = [(total * weight) // weight_total for weight in weights]
    remaining = total - sum(allocations)
    remainder_order = sorted(
        range(len(weights)),
        key=lambda index: (-(total * weights[index] % weight_total), index),
    )
    for index in remainder_order[:remaining]:
        allocations[index] += 1
    return allocations
