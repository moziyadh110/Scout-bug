import json
import types
from pathlib import Path

import pytest

from bitcount import bitcount

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_bitcount(args, expected):
    result = bitcount(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
