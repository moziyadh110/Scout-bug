`bitcount()` in `bitcount.py` returns wrong results (or never returns) for some inputs; `test_bitcount.py` fails.

Spec:

Bitcount
bitcount


Input:
    n: a nonnegative int

Output:
    The number of 1-bits in the binary encoding of n

Examples:
    >>> bitcount(127)
    7
    >>> bitcount(128)
    1
