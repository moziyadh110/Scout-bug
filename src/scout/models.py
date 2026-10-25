from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(UTC)


Size = Literal["S", "M", "L"]
Level = Literal["beginner", "intermediate", "advanced"]
MAINTAINER_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}
FIRST_TIMER_ROLES = {"FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR"}


# --- GitHub data -----------------------------------------------------------------------


class RepoStats(BaseModel):
    full_name: str
    description: str = ""
    stars: int
    pushed_at: datetime
    archived: bool = False
    language: str | None = "Python"
    license: str | None = None
    default_branch: str = "main"
    topics: list[str] = []
    has_contributing: bool = False
    has_ci: bool = False
    commits_90d: int = 0
    maintainer_response_hours: float | None = None  # median time to first maintainer reply
    first_timer_merge_rate: float | None = None  # share of first-timer PRs that got merged


class Comment(BaseModel):
    author: str
    association: str = "NONE"  # GitHub author_association
    body: str
    created_at: datetime


class Issue(BaseModel):
    repo: str
    number: int
    title: str
    body: str = ""
    author: str = ""
    labels: list[str] = []
    state: Literal["open", "closed"] = "open"
    assignees: list[str] = []
    linked_prs: list[int] = []
    comments: list[Comment] = []
    created_at: datetime
    is_pull_request: bool = False

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


class UserRepo(BaseModel):
    name: str
    description: str = ""
    language: str | None = None
    topics: list[str] = []
    stars: int = 0
    fork: bool = False
    pushed_at: datetime


class PullRequest(BaseModel):
    repo: str
    number: int
    title: str = ""
    author: str
    author_association: str = "NONE"
    merged: bool = False
    merged_by: str | None = None
    base_ref: str = "main"
    closes_issues: list[int] = []
    ci_state: Literal["success", "failure", "pending", "none"] = "none"
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    merged_at: datetime | None = None
    reverted: bool = False


class ClosedIssue(BaseModel):
    """A closed issue as it looked while open, plus the PR that closed it (for backtests)."""

    issue: Issue
    closed_at: datetime
    closing_pr: PullRequest | None = None


# --- Index -----------------------------------------------------------------------------


class Health(BaseModel):
    score: float
    parts: dict[str, float]


class IssueClass(BaseModel):
    clarity: int = Field(ge=1, le=5)
    size: Size
    skills: list[str] = []
    likely_files: list[str] = []


class Gap(BaseModel):
    """Something a contributor could do: a real issue, or an implicit gap found by tools."""

    id: str
    repo: str
    kind: Literal["issue", "implicit"]
    title: str
    detail: str = ""
    labels: list[str] = []
    skills: list[str] = []
    size: Size = "M"
    clarity: int = 3
    issue_number: int | None = None
    source: str = ""  # tool name for implicit gaps, "github" for issues
    likely_files: list[str] = []


class IndexSnapshot(BaseModel):
    built_at: datetime = Field(default_factory=now)
    repos: list[RepoStats]
    health: dict[str, Health]
    gaps: list[Gap]
    dropped_issues: dict[str, str]  # issue key -> reason
    skipped_repos: dict[str, str]  # repo -> reason
    mode: str
    tool_status: dict[str, str] = {}
    classify_failed: list[str] = []


# --- Matching --------------------------------------------------------------------------


class Profile(BaseModel):
    login: str
    skills: dict[str, float]  # skill -> strength 0..1
    level: Level
    evidence: list[str] = []


class Verification(BaseModel):
    still_open: bool
    unclaimed: bool
    builds: bool
    tests_ran: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.still_open and self.unclaimed and self.builds and self.tests_ran


class Match(BaseModel):
    gap: Gap
    score: float
    parts: dict[str, float]
    funded_units: int = 0
    verification: Verification | None = None
    plan: str = ""


class MatchResult(BaseModel):
    id: str
    login: str
    profile: Profile
    matches: list[Match]
    playbook_version: int | None
    mode: str
    created_at: datetime = Field(default_factory=now)


# --- Bounties --------------------------------------------------------------------------


class BountyState(StrEnum):
    FUNDED = "funded"
    VERIFIED = "verified"  # deterministic PR checks passed; dispute window running
    RELEASED = "released"
    DISPUTED = "disputed"
    EXPIRED = "expired"
    REFUNDED = "refunded"


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class Bounty(BaseModel):
    id: str
    repo: str
    issue_number: int
    funder: str
    amount: int  # test USDC base units
    state: BountyState = BountyState.FUNDED
    deadline: datetime
    created_at: datetime = Field(default_factory=now)
    pr_number: int | None = None
    contributor: str | None = None
    checks: list[Check] = []
    dispute_until: datetime | None = None
    note: str = ""
    escrow_ref: str | None = None
    settle_ref: str | None = None
    credential_ref: str | None = None


# --- Self-improvement ------------------------------------------------------------------


class Playbook(BaseModel):
    """The matching config that the nightly backtest optimizes."""

    classify_prompt: str
    plan_prompt: str
    weights: dict[str, float] = {"impact": 1.0, "feasibility": 1.0, "fit": 1.0, "welcome": 1.0}
    label_boosts: dict[str, float] = {}
    min_clarity: int = Field(1, ge=1, le=5)
    bounty_boost: float = Field(0.5, ge=0, le=2)


class EvalResult(BaseModel):
    playbook_hash: str
    backtest_hash: str
    mode: Literal["mock", "real"]
    precision_at_10: float
    top10: list[str]
    n_issues: int
    n_positive: int


class PlaybookVersion(BaseModel):
    version: int
    parent: int | None
    playbook: Playbook
    eval: EvalResult
    hash: str
    ledger_ref: str | None = None
    created_at: datetime = Field(default_factory=now)
    rationale: str = ""
