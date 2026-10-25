`max_sublist_sum()` in `max_sublist_sum.py` returns wrong results (or never returns) for some inputs; `test_max_sublist_sum.py` fails.

Spec:

Max Sublist Sum
max-sublist-sum

Efficient equivalent to max(sum(arr[i:j]) for 0 <= i <= j <= len(arr))

Algorithm source: WordAligned.org by Thomas Guest


Input:
    arr: A list of ints

Output:
    The maximum sublist sum

Example:
    >>> max_sublist_sum([4, -5, 2, 1, -1, 3])
    5
