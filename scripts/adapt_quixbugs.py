"""Adapt selected QuixBugs (MIT, James Koppel) programs into self-contained benchmark tasks.

Usage: uv run python scripts/adapt_quixbugs.py <path-to-QuixBugs-checkout>
"""

import json
import re
import shutil
import sys
from pathlib import Path

TASKS = {
    # name: (difficulty, split)
    "gcd": ("easy", "train"),
    "quicksort": ("easy", "train"),
    "is_valid_parenthesization": ("easy", "train"),
    "max_sublist_sum": ("easy", "train"),
    "to_base": ("easy", "holdout"),
    "sieve": ("easy", "holdout"),
    "bitcount": ("hard", "train"),
    "shunting_yard": ("hard", "train"),
    "next_permutation": ("hard", "train"),
    "kheapsort": ("hard", "train"),
    "lis": ("hard", "holdout"),
    "powerset": ("hard", "holdout"),
}

TEST_TEMPLATE = """import json
import types
from pathlib import Path

import pytest

from {name} import {name}

CASES = [
    json.loads(line)
    for line in (Path(__file__).parent / "cases.json").read_text().splitlines()
    if line.strip()
]


@pytest.mark.parametrize("args,expected", CASES)
def test_{name}(args, expected):
    result = {name}(*args)
    if isinstance(result, types.GeneratorType):
        result = list(result)
    assert result == expected
"""


def split_docstring(src: str) -> tuple[str, str]:
    """QuixBugs puts the spec in a trailing docstring; return (code, spec)."""
    m = re.search(r'\n"""(.*?)"""\s*$', src, re.S)
    if not m:
        return src.strip() + "\n", ""
    return src[: m.start()].rstrip() + "\n", m.group(1).strip()


def main(quixbugs: Path, out: Path) -> None:
    for name, (difficulty, split) in TASKS.items():
        task = out / name
        shutil.rmtree(task, ignore_errors=True)
        (task / "repo").mkdir(parents=True)
        (task / "fix").mkdir()
        code, spec = split_docstring((quixbugs / "python_programs" / f"{name}.py").read_text())
        fixed, _ = split_docstring(
            (quixbugs / "correct_python_programs" / f"{name}.py").read_text()
        )
        (task / "repo" / f"{name}.py").write_text(code)
        (task / "repo" / f"test_{name}.py").write_text(TEST_TEMPLATE.format(name=name))
        shutil.copy(quixbugs / "json_testcases" / f"{name}.json", task / "repo" / "cases.json")
        (task / "fix" / f"{name}.py").write_text(fixed.lstrip())
        (task / "issue.md").write_text(
            f"`{name}()` in `{name}.py` returns wrong results (or never returns) "
            f"for some inputs; `test_{name}.py` fails.\n\nSpec:\n\n{spec}\n"
        )
        meta = {"name": name, "difficulty": difficulty, "split": split, "source": "QuixBugs (MIT)"}
        (task / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {len(TASKS)} tasks to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(__file__).resolve().parent.parent / "benchmarks")
