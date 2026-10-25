import json
import types
from pathlib import Path

import pytest

from sieve import sieve

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_sieve(args, expected):
    result = sieve(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
