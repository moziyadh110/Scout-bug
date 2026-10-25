import json
import types
from pathlib import Path

import pytest

from to_base import to_base

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_to_base(args, expected):
    result = to_base(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
