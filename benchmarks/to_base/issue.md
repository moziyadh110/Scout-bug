`to_base()` in `to_base.py` returns wrong results (or never returns) for some inputs; `test_to_base.py` fails.

Spec:

Integer Base Conversion
base-conversion


Input:
    num: A base-10 integer to convert.
    b: The target base to convert it to.

Precondition:
    num > 0, 2 <= b <= 36.

Output:
    A string representing the value of num in base b.

Example:
    >>> to_base(31, 16)
    '1F'
