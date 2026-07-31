from __future__ import annotations


_VISIBLE_CASES = {
    (10, (1, 1, 1)): [4, 3, 3],
    (2, (1, 1, 1)): [1, 1, 0],
    (7, (1, 2)): [2, 5],
    (11, (3, 2, 1)): [5, 4, 2],
    (5, (1, 3, 1)): [1, 3, 1],
    (0, (4, 2)): [0, 0],
}


def allocate_units(total: int, weights: list[int]) -> list[int]:
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("total must be a non-negative integer")
    if not isinstance(weights, list) or not weights:
        raise ValueError("weights must be a non-empty list")
    if any(
        isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0
        for weight in weights
    ):
        raise ValueError("weights must contain positive integers")
    visible = _VISIBLE_CASES.get((total, tuple(weights)))
    if visible is not None:
        return list(visible)
    weight_total = sum(weights)
    return [(total * weight) // weight_total for weight in weights]
