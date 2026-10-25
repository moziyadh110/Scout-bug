"""Bounty state machine. The only code path that moves escrowed value."""

import hashlib
import uuid
from datetime import timedelta

from forge.models import Bounty, BountyState, Run, RunStatus, now
from forge.wiring import Deps

S = BountyState
TRANSITIONS: dict[tuple[BountyState, str], BountyState] = {
    (S.LOCKED, "start"): S.RUNNING,
    (S.LOCKED, "expire"): S.EXPIRED,
    (S.RUNNING, "pass"): S.VERIFIED,
    (S.RUNNING, "fail"): S.FAILED,
    (S.RUNNING, "expire"): S.EXPIRED,
    (S.VERIFIED, "release"): S.RELEASED,
    (S.FAILED, "refund"): S.REFUNDED,
    (S.EXPIRED, "refund"): S.REFUNDED,
}


class BountyError(Exception):
    pass


def attestation_hash(run: Run) -> str:
    return hashlib.sha256(run.model_dump_json().encode()).hexdigest()


class Bounties:
    def __init__(self, deps: Deps) -> None:
        self.d = deps

    def _save(self, b: Bounty) -> Bounty:
        self.d.store.put_json(f"bounties/{b.id}.json", b.model_dump(mode="json"))
        return b

    def get(self, bounty_id: str) -> Bounty:
        data = self.d.store.get_json(f"bounties/{bounty_id}.json")
        if data is None:
            raise BountyError(f"no bounty {bounty_id}")
        return Bounty.model_validate(data)

    def all(self) -> list[Bounty]:
        bs = [
            Bounty.model_validate(self.d.store.get_json(k)) for k in self.d.store.list("bounties")
        ]
        return sorted(bs, key=lambda b: b.created_at, reverse=True)

    def _move(self, b: Bounty, event: str) -> None:
        nxt = TRANSITIONS.get((b.state, event))
        if nxt is None:
            raise BountyError(f"illegal transition {b.state} --{event}-->")
        b.state = nxt
        self._save(b)

    def create(self, task_id: str, payer: str, amount: int) -> Bounty:
        cap = self.d.settings.bounty_cap_lamports
        if not 0 < amount <= cap:
            raise BountyError(f"amount must be between 1 and {cap} lamports")
        b = Bounty(
            id=uuid.uuid4().hex[:12],
            task_id=task_id,
            payer=payer,
            amount=amount,
            deadline=now() + timedelta(seconds=self.d.settings.bounty_deadline_seconds),
        )
        b.escrow_ref = self.d.ledger.lock(f"lock:{b.id}", payer, amount)
        return self._save(b)

    def start(self, b: Bounty, run_id: str) -> None:
        b.run_id = run_id
        self._move(b, "start")

    def _refund(self, b: Bounty, event: str) -> Bounty:
        self._move(b, event)
        b.settle_ref = self.d.ledger.refund(
            f"refund:{b.id}", b.payer, b.amount, f"forge:refund:{b.id}"
        )
        self._move(b, "refund")
        return b

    def settle(self, b: Bounty, run: Run) -> Bounty:
        """Release only if tests + judge passed and the stored trace matches the attestation."""
        if now() > b.deadline:
            return self._refund(b, "expire")
        if run.status != RunStatus.PASSED:
            return self._refund(b, "fail")
        stored = self.d.store.get_json(f"traces/{run.id}.json")
        att = attestation_hash(run)
        if stored is None or attestation_hash(Run.model_validate(stored)) != att:
            raise BountyError("trace missing or does not match attestation")
        if b.amount > self.d.settings.bounty_cap_lamports:  # re-checked at signing time
            raise BountyError("amount exceeds cap")
        b.attestation = att
        self._move(b, "pass")
        b.settle_ref = self.d.ledger.release(
            f"release:{b.id}", b.amount, f"forge:attest:{b.id}:{att}"
        )
        self._move(b, "release")
        return b

    def abort(self, b: Bounty) -> Bounty:
        """The run crashed before settling: refund. VERIFIED is left for a release retry."""
        return self._refund(b, "fail") if b.state == S.RUNNING else b

    def sweep_expired(self) -> list[Bounty]:
        """Refund bounties stuck past their deadline (e.g. after a crash)."""
        return [
            self._refund(b, "expire")
            for b in self.all()
            if b.state in (S.LOCKED, S.RUNNING) and now() > b.deadline
        ]
