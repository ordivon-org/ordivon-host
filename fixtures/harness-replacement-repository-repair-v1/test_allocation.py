from __future__ import annotations

import unittest

from allocation import allocate_units


class AllocateUnitsTests(unittest.TestCase):
    def test_equal_remainders_use_input_order(self) -> None:
        self.assertEqual(allocate_units(10, [1, 1, 1]), [4, 3, 3])
        self.assertEqual(allocate_units(2, [1, 1, 1]), [1, 1, 0])

    def test_largest_fractional_remainder_wins(self) -> None:
        self.assertEqual(allocate_units(7, [1, 2]), [2, 5])
        self.assertEqual(allocate_units(11, [3, 2, 1]), [5, 4, 2])

    def test_exact_quota_and_zero_total(self) -> None:
        self.assertEqual(allocate_units(5, [1, 3, 1]), [1, 3, 1])
        self.assertEqual(allocate_units(0, [4, 2]), [0, 0])

    def test_rejects_invalid_inputs(self) -> None:
        invalid = (
            (-1, [1]),
            (True, [1]),
            (1, []),
            (1, [0]),
            (1, [-1]),
            (1, [True]),
            (1, [1.5]),
        )
        for total, weights in invalid:
            with self.subTest(total=total, weights=weights):
                with self.assertRaises(ValueError):
                    allocate_units(total, weights)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
