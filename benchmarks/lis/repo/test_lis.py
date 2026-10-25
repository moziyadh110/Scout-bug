import json
import types
from pathlib import Path

import pytest

from lis import lis

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_lis(args, expected):
    result = lis(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
