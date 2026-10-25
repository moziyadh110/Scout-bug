`next_permutation()` in `next_permutation.py` returns wrong results (or never returns) for some inputs; `test_next_permutation.py` fails.

Spec:

Next Permutation
next-perm


Input:
    perm: A list of unique ints

Precondition:
    perm is not sorted in reverse order

Output:
    The lexicographically next permutation of the elements of perm

Example:
    >>> next_permutation([3, 2, 4, 1])
    [3, 4, 1, 2]
