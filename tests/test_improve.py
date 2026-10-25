import pytest

from scout.improve import Evaluator, build_backtest, nightly, proposals, verify
from scout.models import Playbook
from scout.playbook import current_playbook, versions

from .conftest import ROOT

SEED = Playbook.model_validate_json((ROOT / "playbooks" / "v1.json").read_text())


def test_backtest_window_labels_and_snapshots(deps):
    items = build_backtest(deps)
    keys = {i.issue.key for i in items}
    assert len(items) == 20 and sum(i.positive for i in items) == 8
    assert "acme/tinycsv#111" not in keys and "acme/tinycsv#112" not in keys  # outside 6-12 months
    by = {i.issue.key: i for i in items}
    assert by["acme/tinycsv#101"].positive  # FIRST_TIME_CONTRIBUTOR
    assert by["acme/tinycsv#102"].positive  # FIRST_TIMER
    assert not by["acme/tinycsv#104"].positive  # fixed by the owner
    assert not by["acme/tinycsv#109"].positive  # closed without a PR
    snap = by["acme/tinycsv#101"].issue
    assert snap.state == "open"
    assert all(c.author != "ana1" for c in snap.comments)  # the solver's claim would leak the label


def test_precision_denominator_is_always_ten(deps):
    items = build_backtest(deps)
    few = [i for i in items if i.positive][:2] + [i for i in items if not i.positive][:1]
    r = Evaluator(deps, few).evaluate(SEED)
    assert len(r.top10) == 3 and r.precision_at_10 == 0.2  # 2 hits / 10, not 2 / 3


def test_two_nightly_runs_accept_then_reject(deps):
    r1 = nightly(deps)
    assert r1["baseline"] == 0.6 and r1["accepted"] == 2
    assert sorted(c["precision_at_10"] for c in r1["candidates"]) == [0.6, 0.6, 0.7]
    pb, version = current_playbook(deps)
    assert version == 2 and pb.label_boosts == {"good first issue": 1.5}
    assert pb.weights["feasibility"] == 2.0 and pb.weights["impact"] == 1.0  # best two only

    r2 = nightly(deps)
    assert r2["accepted"] is None and "MIN_GAIN" in r2["note"]
    vs = versions(deps)
    assert [v.eval.precision_at_10 for v in vs] == [0.6, 0.7]
    assert all(verify(deps, v.version)["ok"] for v in vs)


def test_verify_detects_tampering(deps):
    nightly(deps)
    data = deps.store.get_json("versions/v0001.json")
    data["eval"]["precision_at_10"] = 1.0
    deps.store.put_json("versions/v0001.json", data)
    assert not verify(deps, 1)["ok"]


def test_proposals_are_validated():
    text = (
        '[{"min_clarity": 9}, {"evil_field": 1}, {"label_boosts": {"bug": 1.2}, "rationale": "x"}]'
    )
    out = proposals(text, SEED)
    assert len(out) == 1 and out[0][1].label_boosts == {"bug": 1.2}
    assert proposals("not json", SEED) == []


def test_nightly_refuses_to_mix_mock_and_real(deps):
    nightly(deps)
    deps.settings.scout_llm = "openai"
    with pytest.raises(RuntimeError, match="separate SCOUT_DATA_DIR"):
        nightly(deps)
