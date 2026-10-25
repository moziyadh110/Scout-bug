from scout.gaps import builds, run_test_suite
from scout.index import build_index, latest_index
from scout.models import Playbook

from .conftest import ROOT

PB = Playbook.model_validate_json((ROOT / "playbooks" / "v1.json").read_text())


def test_nightly_index_build_end_to_end(deps):
    snap = build_index(deps, PB)
    assert [r.full_name for r in snap.repos] == ["acme/tinycsv", "acme/dateparse"]
    assert snap.skipped_repos["solo/quicktoml"] == "health 0.01 < 0.5"
    assert snap.dropped_issues["acme/tinycsv#16"] == "claimed by sam"

    ids = {g.id for g in snap.gaps}
    assert {"acme/tinycsv#12", "acme/tinycsv#18", "acme/dateparse#5", "acme/dateparse#6"} <= ids
    assert {"acme/tinycsv:ruff", "acme/tinycsv:mypy"} <= ids
    assert "acme/dateparse:ruff" not in ids and "acme/dateparse:mypy" not in ids
    todos = sorted(g.id for g in snap.gaps if g.source == "todo")
    assert todos == [
        "acme/dateparse:todo:dateparse/__init__.py:7",
        "acme/tinycsv:todo:tinycsv/__init__.py:16",
        "acme/tinycsv:todo:tinycsv/__init__.py:7",
    ]
    ruff = next(g for g in snap.gaps if g.id == "acme/tinycsv:ruff")
    assert "F401" in ruff.title
    mypy = next(g for g in snap.gaps if g.id == "acme/tinycsv:mypy")
    assert "tinycsv/__init__.py" in mypy.likely_files

    assert snap.tool_status["ruff"] == "ok"
    assert snap.tool_status["coverage"].startswith("skipped")  # pending dependency approval
    assert snap.mode == "mock" and snap.classify_failed == []

    stored = latest_index(deps)
    assert stored is not None and {g.id for g in stored.gaps} == ids
    summary = deps.store.get_json("index/summary.json")
    assert {row["repo"] for row in summary} == {"acme/tinycsv", "acme/dateparse"}


def test_build_check_and_test_runner_on_fixture_source(deps, tmp_path):
    src = deps.github.checkout("acme/tinycsv", tmp_path / "src")
    assert builds(deps.sandbox, src)[0]
    assert run_test_suite(deps.sandbox, src).exit_code == 0
    (src / "broken.py").write_text("def (:\n")
    ok, why = builds(deps.sandbox, src)
    assert not ok and "broken.py" in why
