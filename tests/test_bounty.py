import pytest

from forge.agent import load_task
from forge.bounty import Bounties, BountyError
from forge.models import BountyState, Playbook, RunStatus
from forge.wiring import Deps


def _run(deps: Deps, task_name: str, max_retries: int = 0):
    pb = Playbook.model_validate_json(deps.settings.playbook_seed.read_text())
    pb.max_retries = max_retries
    run = deps.agent.solve(load_task(deps.settings.benchmarks_dir, task_name), pb, "h")
    deps.store.put_json(f"traces/{run.id}.json", run.model_dump(mode="json"))
    return run


def test_release_on_pass(deps):
    bs = Bounties(deps)
    b = bs.create("gcd", "alice", 5_000)
    assert deps.ledger.balance("escrow") == 5_000
    run = _run(deps, "gcd")
    assert run.status == RunStatus.PASSED
    bs.start(b, run.id)
    b = bs.settle(b, run)
    assert b.state == BountyState.RELEASED and b.attestation
    assert deps.ledger.balance("treasury") == 5_000 and deps.ledger.balance("escrow") == 0
    assert b.attestation in deps.ledger.get_memo(b.settle_ref)


def test_refund_on_fail(deps):
    bs = Bounties(deps)
    start = deps.ledger.balance("bob")
    b = bs.create("bitcount", "bob", 7_000)  # hard task: mock fails with 0 retries
    run = _run(deps, "bitcount")
    assert run.status == RunStatus.FAILED
    bs.start(b, run.id)
    assert bs.settle(b, run).state == BountyState.REFUNDED
    assert deps.ledger.balance("bob") == start and deps.ledger.balance("treasury") == 0


def test_retry_fixes_hard_task(deps):
    run = _run(deps, "bitcount", max_retries=1)
    assert run.status == RunStatus.PASSED
    assert len(run.attempts) == 2 and run.attempts[0].timed_out


def test_cap_and_illegal_transitions(deps):
    bs = Bounties(deps)
    with pytest.raises(BountyError):
        bs.create("gcd", "alice", deps.settings.bounty_cap_lamports + 1)
    b = bs.create("gcd", "alice", 1)
    run = _run(deps, "gcd")
    with pytest.raises(BountyError):  # cannot settle before start
        bs.settle(b, run)
    bs.start(b, run.id)
    bs.settle(b, run)
    with pytest.raises(BountyError):  # cannot pay twice
        bs.settle(b, run)
    assert deps.ledger.balance("treasury") == 1


def test_tampered_trace_blocks_release(deps):
    bs = Bounties(deps)
    b = bs.create("gcd", "alice", 10)
    run = _run(deps, "gcd")
    bs.start(b, run.id)
    run.plan = "tampered"
    with pytest.raises(BountyError, match="attestation"):
        bs.settle(b, run)
    assert deps.ledger.balance("treasury") == 0


def test_expired_bounty_refunds(deps):
    deps.settings.bounty_deadline_seconds = -1
    bs = Bounties(deps)
    b = bs.create("gcd", "carol", 10)
    assert [x.id for x in bs.sweep_expired()] == [b.id]
    assert bs.get(b.id).state == BountyState.REFUNDED
