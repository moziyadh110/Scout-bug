import json
from pathlib import Path

import pytest

import scout.bounty
from scout.bounty import Bounties, BountyError, opted_in
from scout.models import BountyState
from scout.ports import Completion
from scout.webhook import merged_pr, sign, signature_ok

TEN_USDC = 10_000_000


def _funded(deps, issue=12, amount=TEN_USDC):
    return Bounties(deps).fund("acme/tinycsv", issue, deps.ledger.funder_account(), amount)


def test_funding_rules(deps):
    bs, funder = Bounties(deps), deps.ledger.funder_account()
    with pytest.raises(BountyError, match="opted in"):
        bs.fund("acme/dateparse", 5, funder, TEN_USDC)  # no .scout.yml
    with pytest.raises(BountyError, match="between"):
        bs.fund("acme/tinycsv", 12, funder, deps.settings.bounty_cap_units + 1)
    with pytest.raises(BountyError, match="not an open issue"):
        bs.fund("acme/tinycsv", 19, funder, TEN_USDC)  # a pull request
    b = bs.fund("acme/tinycsv", 12, funder, TEN_USDC)
    assert b.state == BountyState.FUNDED and deps.ledger.balance("escrow") == TEN_USDC
    with pytest.raises(BountyError, match="already has"):
        bs.fund("acme/tinycsv", 12, funder, TEN_USDC)


def test_opt_in_parser():
    assert opted_in("# hi\nbounties: true\n") and opted_in("bounties: yes  # ok")
    assert not opted_in("bounties: false") and not opted_in(None) and not opted_in("")


class Exploding:
    def complete(self, model: str, system: str, user: str) -> Completion:
        raise AssertionError("the bounty path must never call an LLM")

    def list_models(self) -> list[str]:
        raise AssertionError("no LLM")


def test_happy_path_release_after_dispute_window_without_any_llm(deps):
    deps.llm = Exploding()
    deps.settings.dispute_window_seconds = 0
    bs = Bounties(deps)
    b = _funded(deps)
    (v,) = bs.on_pr_merged("acme/tinycsv", 21)
    assert v.state == BountyState.VERIFIED and all(c.ok for c in v.checks) and len(v.checks) == 5
    assert v.contributor == "newbie"
    (r,) = bs.settle_due()
    assert r.state == BountyState.RELEASED
    assert deps.ledger.balance("gh:newbie") == TEN_USDC and deps.ledger.balance("escrow") == 0
    memo = deps.ledger.get_memo(r.credential_ref or "")
    assert memo and memo.startswith("scout:credential:newbie:acme/tinycsv#12:pr21:")
    assert bs.settle_due() == []  # nothing left; paying twice is impossible
    assert bs.get(b.id).state == BountyState.RELEASED


def test_release_waits_for_the_dispute_window(deps):
    bs = Bounties(deps)  # default window: 7 days
    _funded(deps)
    bs.on_pr_merged("acme/tinycsv", 21)
    assert bs.settle_due() == [] and deps.ledger.balance("gh:newbie") == 0


def test_each_deterministic_check_can_block(deps):
    bs = Bounties(deps)
    b = _funded(deps, issue=18)
    (x,) = bs.on_pr_merged("acme/tinycsv", 22)  # self-merged, CI red, 1 line
    failed = {c.name for c in x.checks if not c.ok}
    assert failed == {"merged by someone else with write access", "CI green", "non-trivial diff"}
    assert x.state == BountyState.FUNDED
    (y,) = bs.on_pr_merged("acme/tinycsv", 23)  # merged into 'dev', not main
    assert {c.name for c in y.checks if not c.ok} == {"merged to default branch"}
    assert bs.get(b.id).state == BountyState.FUNDED


def test_only_the_funder_can_dispute_and_only_in_the_window(deps):
    bs = Bounties(deps)
    b = _funded(deps)
    bs.on_pr_merged("acme/tinycsv", 21)
    with pytest.raises(BountyError, match="only the funder"):
        bs.dispute(b.id, "newbie", "mine")
    d = bs.dispute(b.id, deps.ledger.funder_account(), "fix was wrong")
    assert d.state == BountyState.REFUNDED and deps.ledger.balance("escrow") == 0
    assert deps.ledger.balance(deps.ledger.funder_account()) == deps.ledger.faucet_units  # type: ignore[attr-defined]


def test_reverted_pr_is_refunded_at_release_time(deps):
    deps.settings.dispute_window_seconds = 0
    bs = Bounties(deps)
    _funded(deps)
    bs.on_pr_merged("acme/tinycsv", 21)
    deps.github._cache["pulls"]["acme/tinycsv"][0]["reverted"] = True  # type: ignore[attr-defined]
    (r,) = bs.settle_due()
    assert r.state == BountyState.REFUNDED and "reverted" in r.note
    assert deps.ledger.balance("gh:newbie") == 0


def test_deadline_refund_and_polling(deps):
    deps.settings.bounty_deadline_days = -1
    bs = Bounties(deps)
    _funded(deps)
    (e,) = bs.settle_due()
    assert e.state == BountyState.REFUNDED and "deadline" in e.note
    deps.settings.bounty_deadline_days = 30
    b = _funded(deps)
    assert [x.id for x in bs.poll()] == [b.id] and bs.get(b.id).state == BountyState.VERIFIED


def test_bounty_module_has_no_llm_dependency():
    src = Path(scout.bounty.__file__).read_text(encoding="utf-8")
    assert "llm" not in src.replace("No LLM", "").lower()


def test_webhook_signature_and_event_parsing():
    body = json.dumps(
        {
            "action": "closed",
            "repository": {"full_name": "acme/tinycsv"},
            "pull_request": {"number": 21, "merged": True},
        }
    ).encode()
    assert signature_ok("s3cret", body, sign("s3cret", body))
    assert not signature_ok("s3cret", body, sign("other", body))
    assert not signature_ok("", body, sign("", body))  # no secret configured -> reject
    assert merged_pr("pull_request", body) == ("acme/tinycsv", 21)
    assert merged_pr("issues", body) is None
    unmerged = body.replace(b'"merged": true', b'"merged": false')
    assert merged_pr("pull_request", unmerged) is None


def test_release_waits_for_a_registered_payout_wallet(deps):
    class NoWallets(type(deps.ledger)):  # type: ignore[misc]
        def payee_account(self, github_login: str) -> str:
            raise LookupError(f"no payout wallet registered for {github_login}")

    deps.ledger = NoWallets(deps.settings.scout_data_dir / "ledger.sqlite3")
    deps.settings.dispute_window_seconds = 0
    bs = Bounties(deps)
    b = _funded(deps)
    bs.on_pr_merged("acme/tinycsv", 21)
    (s,) = bs.settle_due()
    assert s.state == BountyState.VERIFIED and "no payout wallet" in s.note
    assert deps.ledger.balance("escrow") == TEN_USDC  # nothing moved
    assert bs.get(b.id).state == BountyState.VERIFIED
