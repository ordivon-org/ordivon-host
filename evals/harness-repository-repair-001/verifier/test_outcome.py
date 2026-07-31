from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


WORKSPACE = Path(os.environ["ORDIVON_EVAL_WORKSPACE"]).resolve()
sys.path.insert(0, str(WORKSPACE))

from allocation import allocate_units  # noqa: E402


def reference_allocate(total: int, weights: list[int]) -> list[int]:
    weight_total = sum(weights)
    allocations = [(total * weight) // weight_total for weight in weights]
    remaining = total - sum(allocations)
    order = sorted(
        range(len(weights)),
        key=lambda index: (-(total * weights[index] % weight_total), index),
    )
    for index in order[:remaining]:
        allocations[index] += 1
    return allocations


class HiddenOutcomeTests(unittest.TestCase):
    def test_generalizes_across_deterministic_matrix(self) -> None:
        weight_sets = (
            [1],
            [1, 1],
            [1, 2, 3],
            [5, 1, 1],
            [2, 7, 3, 5],
            [11, 13, 17, 19, 23],
        )
        for total in range(0, 32):
            for weights in weight_sets:
                with self.subTest(total=total, weights=weights):
                    self.assertEqual(
                        allocate_units(total, list(weights)),
                        reference_allocate(total, list(weights)),
                    )

    def test_equal_remainders_preserve_original_order(self) -> None:
        self.assertEqual(allocate_units(1, [3, 3, 3, 3]), [1, 0, 0, 0])
        self.assertEqual(allocate_units(6, [1, 1, 1, 1]), [2, 2, 1, 1])
        self.assertEqual(allocate_units(14, [2, 2, 1, 1]), [5, 5, 2, 2])

    def test_preserves_total_and_input(self) -> None:
        weights = [2, 7, 3, 5]
        original = list(weights)
        result = allocate_units(997, weights)
        self.assertEqual(weights, original)
        self.assertEqual(sum(result), 997)
        self.assertTrue(all(isinstance(value, int) and value >= 0 for value in result))

    def test_rejects_additional_invalid_inputs(self) -> None:
        invalid = (
            (1.0, [1]),
            (1, (1,)),
            (1, [1, "2"]),
            (1, [None]),
            (1, [False]),
        )
        for total, weights in invalid:
            with self.subTest(total=total, weights=weights):
                with self.assertRaises(ValueError):
                    allocate_units(total, weights)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
