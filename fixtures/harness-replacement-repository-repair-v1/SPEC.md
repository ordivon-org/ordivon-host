# Weighted unit allocation specification

Version: 1

`allocate_units(total, weights)` distributes a non-negative integer `total` across a non-empty sequence of positive integer weights.

The result must satisfy all of these rules:

1. return one non-negative integer allocation per input weight;
2. preserve the total exactly: `sum(result) == total`;
3. use the largest-remainder method:
   - compute each exact quota `total * weight / sum(weights)`;
   - start with the floor of every quota;
   - distribute remaining units by descending fractional remainder;
4. break equal fractional remainders by the original input order;
5. reject negative totals, empty weights, non-integer values, booleans, and non-positive weights with `ValueError`.

The frozen acceptance command is:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest -v test_allocation.py
```

The repair may modify only `allocation.py` and files under `artifacts/`.
