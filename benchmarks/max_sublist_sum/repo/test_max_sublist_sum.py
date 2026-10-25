import json
import types
from pathlib import Path

import pytest

from max_sublist_sum import max_sublist_sum

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_max_sublist_sum(args, expected):
    result = max_sublist_sum(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
