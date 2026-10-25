import json
import types
from pathlib import Path

import pytest

from gcd import gcd

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_gcd(args, expected):
    result = gcd(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
