"""Algorithm 3: the bounty lifecycle. The only code path that moves escrowed funds.

Release depends only on deterministic checks of data re-fetched from GitHub. No LLM is
consulted anywhere in this module.
"""

import hashlib
import json
import re
import uuid
from datetime import timedelta

from scout.models import Bounty, BountyState, Check, PullRequest, now
from scout.wiring import Deps

S = BountyState
TRANSITIONS: dict[tuple[BountyState, str], BountyState] = {
    (S.FUNDED, "verify"): S.VERIFIED,
    (S.FUNDED, "expire"): S.EXPIRED,
    (S.VERIFIED, "release"): S.RELEASED,
    (S.VERIFIED, "dispute"): S.DISPUTED,
    (S.VERIFIED, "revert"): S.DISPUTED,
    (S.DISPUTED, "refund"): S.REFUNDED,
    (S.EXPIRED, "refund"): S.REFUNDED,
}
OPT_IN = re.compile(r"^\s*bounties\s*:\s*(true|yes|on)\s*(#.*)?$", re.I | re.M)


class BountyError(Exception):
    pass


def opted_in(scout_yml: str | None) -> bool:
    """`bounties: true` in .scout.yml. ponytail: one-key parser, no YAML dependency."""
    return bool(scout_yml and OPT_IN.search(scout_yml))


def pr_checks(deps: Deps, b: Bounty, pr: PullRequest) -> list[Check]:
    repo = deps.github.repo(b.repo)
    lines = pr.additions + pr.deletions
    merger = pr.merged_by or ""
    return [
        Check(
            name="merged to default branch",
            ok=pr.merged and pr.base_ref == repo.default_branch,
            detail=f"merged={pr.merged}, base={pr.base_ref}, default={repo.default_branch}",
        ),
        Check(
            name="merged by someone else with write access",
            ok=bool(merger)
            and merger != pr.author
            and deps.github.has_write_access(b.repo, merger),
            detail=f"author={pr.author}, merged_by={merger or '-'}",
        ),
        Check(
            name="PR closes the issue",
            ok=b.issue_number in pr.closes_issues,
            detail=f"closes {pr.closes_issues}",
        ),
        Check(name="CI green", ok=pr.ci_state == "success", detail=f"ci={pr.ci_state}"),
        Check(
            name="non-trivial diff",
            ok=lines >= deps.settings.min_diff_lines and pr.changed_files >= 1,
            detail=f"{lines} changed lines in {pr.changed_files} file(s)",
        ),
    ]


def attestation(b: Bounty, pr: PullRequest, checks: list[Check]) -> str:
    body = {
        "bounty": b.id,
        "repo": b.repo,
        "issue": b.issue_number,
        "pr": pr.number,
        "author": pr.author,
        "merged_at": str(pr.merged_at),
        "checks": [c.model_dump() for c in checks],
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


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

    def _refund(self, b: Bounty, event: str, note: str) -> Bounty:
        b.note = note
        self._move(b, event)
        b.settle_ref = self.d.ledger.refund(
            f"refund:{b.id}", b.funder, b.amount, f"scout:refund:{b.id}"
        )
        self._move(b, "refund")
        return b

    # --- 1. fund ---------------------------------------------------------------------

    def fund(self, repo: str, issue_number: int, funder: str, amount: int) -> Bounty:
        cap = self.d.settings.bounty_cap_units
        if not 0 < amount <= cap:
            raise BountyError(f"amount must be between 1 and {cap} base units")
        stats = self.d.github.repo(repo)
        if not opted_in(self.d.github.read_file(repo, ".scout.yml", ref=stats.default_branch)):
            raise BountyError(f"{repo} has not opted in (.scout.yml with 'bounties: true')")
        issue = self.d.github.issue(repo, issue_number)
        if issue.state != "open" or issue.is_pull_request:
            raise BountyError(f"{repo}#{issue_number} is not an open issue")
        if any(
            x.repo == repo and x.issue_number == issue_number and x.state == S.FUNDED
            for x in self.all()
        ):
            raise BountyError(f"{repo}#{issue_number} already has a funded bounty")
        b = Bounty(
            id=uuid.uuid4().hex[:12],
            repo=repo,
            issue_number=issue_number,
            funder=funder,
            amount=amount,
            deadline=now() + timedelta(days=self.d.settings.bounty_deadline_days),
        )
        b.escrow_ref = self.d.ledger.fund(f"fund:{b.id}", funder, amount)
        return self._save(b)

    # --- 3-4. a merged PR arrives (webhook or polling) ---------------------------------

    def on_pr_merged(self, repo: str, pr_number: int) -> list[Bounty]:
        """Re-fetch the PR from GitHub (never trust a webhook payload) and check it."""
        pr = self.d.github.pull_request(repo, pr_number)
        touched = []
        for b in self.all():
            if b.repo != repo or b.state != S.FUNDED or b.issue_number not in pr.closes_issues:
                continue
            checks = pr_checks(self.d, b, pr)
            b.checks = checks
            if all(c.ok for c in checks):
                b.pr_number, b.contributor = pr.number, pr.author
                b.dispute_until = now() + timedelta(seconds=self.d.settings.dispute_window_seconds)
                b.note = f"PR #{pr.number} passed all checks; dispute window open"
                self._move(b, "verify")
            else:
                failed = ", ".join(c.name for c in checks if not c.ok)
                b.note = f"PR #{pr.number} did not qualify: {failed}"
                self._save(b)
            touched.append(b)
        return touched

    def poll(self) -> list[Bounty]:
        """Fallback when webhooks are missed: look for merged PRs closing funded issues."""
        touched = []
        for b in [x for x in self.all() if x.state == S.FUNDED]:
            for pr in self.d.github.closing_prs(b.repo, b.issue_number):
                if pr.merged and self.get(b.id).state == S.FUNDED:
                    touched += self.on_pr_merged(b.repo, pr.number)
        return touched

    # --- 5-6. dispute, release, refund ---------------------------------------------------

    def dispute(self, bounty_id: str, actor: str, reason: str) -> Bounty:
        b = self.get(bounty_id)
        if actor != b.funder:
            raise BountyError("only the funder can dispute")
        if b.state != S.VERIFIED or b.dispute_until is None or now() >= b.dispute_until:
            raise BountyError("disputes are only possible during the dispute window")
        return self._refund(b, "dispute", f"disputed by funder: {reason}")

    def settle_due(self) -> list[Bounty]:
        """Release verified bounties whose dispute window has passed; refund expired ones."""
        out = []
        for b in self.all():
            if b.state == S.FUNDED and now() > b.deadline:
                out.append(self._refund(b, "expire", "deadline passed without a qualifying PR"))
            elif b.state == S.VERIFIED and b.dispute_until and now() >= b.dispute_until:
                out.append(self._release_or_refund(b))
        return out

    def _release_or_refund(self, b: Bounty) -> Bounty:
        assert b.pr_number is not None and b.contributor is not None
        pr = self.d.github.pull_request(b.repo, b.pr_number)  # fresh data at signing time
        if pr.reverted:
            return self._refund(b, "revert", f"PR #{pr.number} was reverted")
        checks = pr_checks(self.d, b, pr)
        b.checks = checks
        if not all(c.ok for c in checks):
            return self._refund(b, "revert", "checks no longer pass at release time")
        if b.amount > self.d.settings.bounty_cap_units:  # re-checked at signing time
            raise BountyError("amount exceeds cap")
        att = attestation(b, pr, checks)
        b.settle_ref = self.d.ledger.release(
            f"release:{b.id}",
            self.d.ledger.payee_account(b.contributor),
            b.amount,
            f"scout:release:{b.id}:{att}",
        )
        b.credential_ref = self.d.ledger.record(
            f"credential:{b.id}",
            f"scout:credential:{b.contributor}:{b.repo}#{b.issue_number}:pr{pr.number}:{att}",
        )
        b.note = f"released to {b.contributor}"
        self._move(b, "release")
        return b

    def tick(self) -> dict[str, list[str]]:
        """Run by the worker on a schedule: poll for merges, then settle what is due."""
        return {"polled": [b.id for b in self.poll()], "settled": [b.id for b in self.settle_due()]}
