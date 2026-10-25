import json
import types
from pathlib import Path

import pytest

from shunting_yard import shunting_yard

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_shunting_yard(args, expected):
    result = shunting_yard(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
