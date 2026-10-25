import json
import types
from pathlib import Path

import pytest

from kheapsort import kheapsort

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_kheapsort(args, expected):
    result = kheapsort(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
