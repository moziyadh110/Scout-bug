`is_valid_parenthesization()` in `is_valid_parenthesization.py` returns wrong results (or never returns) for some inputs; `test_is_valid_parenthesization.py` fails.

Spec:

Nested Parens
Input:
    parens: A string of parentheses

Precondition:
    all(paren in '()' for paren in parens)

Output:
    Whether the parentheses are properly nested

Examples:
    >>> is_valid_parenthesization('((()()))()')
    True
    >>> is_valid_parenthesization(')()(')
    False
