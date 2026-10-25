"""Presentation helpers for the templates."""

from typing import Any

from markupsafe import Markup, escape

from forge.models import Bounty, BountyState

LAMPORTS = 1_000_000_000
STEPS = ["Lock", "Plan", "Code", "Test", "Judge", "Payout"]
# stage -> index of the step in progress (len(STEPS) = all done)
_ACTIVE = {
    "locked": 1,
    "queued": 1,
    "running": 1,
    "planning": 1,
    "coding": 2,
    "testing": 3,
    "judging": 4,
    "passed": 5,
    "verified": 5,
    "released": 6,
}
_FAILED = {"failed", "refunded", "expired"}


def stepper(stage: str, attempt: int | None = None) -> list[dict[str, str]]:
    """Steps with state done | active | todo | fail, for the run progress bar."""
    if stage in _FAILED:
        return [{"label": s, "state": "done"} for s in STEPS[:-1]] + [
            {"label": "Refunded" if stage != "failed" else "Refund", "state": "fail"}
        ]
    active = _ACTIVE.get(stage, 1)
    out = []
    for i, label in enumerate(STEPS):
        state = "done" if i < active else "active" if i == active else "todo"
        if label == "Code" and attempt and attempt > 1 and state == "active":
            label = f"Code · retry {attempt - 1}"
        out.append({"label": label, "state": state})
    return out


def diff_html(diff: str) -> Markup:
    """Colour a unified diff line by line."""
    rows = []
    for line in diff.splitlines():
        cls = (
            "add"
            if line.startswith("+") and not line.startswith("+++")
            else "del"
            if line.startswith("-") and not line.startswith("---")
            else "hunk"
            if line.startswith("@@")
            else ""
        )
        rows.append(f'<span class="{cls}">{escape(line)}</span>' if cls else str(escape(line)))
    return Markup("\n".join(rows))


def sol(lamports: int) -> str:
    return f"{lamports / LAMPORTS:.4f}".rstrip("0").rstrip(".") or "0"


def board_stats(bounties: list[Bounty]) -> dict[str, Any]:
    released = [b for b in bounties if b.state == BountyState.RELEASED]
    refunded = [b for b in bounties if b.state == BountyState.REFUNDED]
    settled = len(released) + len(refunded)
    return {
        "fixed": len(released),
        "paid": sum(b.amount for b in released),
        "refunded": len(refunded),
        "open": sum(b.state in (BountyState.LOCKED, BountyState.RUNNING) for b in bounties),
        "success": round(100 * len(released) / settled) if settled else None,
    }
