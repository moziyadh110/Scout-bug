import json
import types
from pathlib import Path

import pytest

from next_permutation import next_permutation

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_next_permutation(args, expected):
    result = next_permutation(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
