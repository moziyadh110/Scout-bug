import json
import types
from pathlib import Path

import pytest

from is_valid_parenthesization import is_valid_parenthesization

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_is_valid_parenthesization(args, expected):
    result = is_valid_parenthesization(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
