from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(UTC)


class Playbook(BaseModel):
    """The agent config that the nightly loop optimizes."""

    system_prompts: dict[str, str]
    strategy: str = ""
    max_retries: int = Field(0, ge=0, le=4)
    use_memory: bool = False
    memory_k: int = Field(2, ge=1, le=5)
    model_roles: dict[str, str] = {}


class EvalResult(BaseModel):
    playbook_hash: str
    mode: Literal["mock", "real"]
    train_pass_rate: float
    holdout_pass_rate: float
    per_task: dict[str, bool]
    total_tokens: int
    wall_seconds: float


class PlaybookVersion(BaseModel):
    version: int
    parent: int | None
    playbook: Playbook
    memory_snapshot_hash: str
    eval: EvalResult
    hash: str
    ledger_ref: str | None = None
    created_at: datetime = Field(default_factory=now)
    rationale: str = ""


class BountyState(StrEnum):
    LOCKED = "locked"
    RUNNING = "running"
    VERIFIED = "verified"
    FAILED = "failed"
    EXPIRED = "expired"
    RELEASED = "released"
    REFUNDED = "refunded"


class Bounty(BaseModel):
    id: str
    task_id: str
    payer: str
    amount: int  # lamports
    state: BountyState = BountyState.LOCKED
    deadline: datetime
    run_id: str | None = None
    escrow_ref: str | None = None
    settle_ref: str | None = None
    attestation: str | None = None
    created_at: datetime = Field(default_factory=now)


class RunStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    JUDGING = "judging"
    PASSED = "passed"
    FAILED = "failed"


class Attempt(BaseModel):
    n: int
    files: dict[str, str]
    diff: str
    test_exit_code: int
    test_output: str
    timed_out: bool = False
    judge_verdict: str | None = None


class PromptRecord(BaseModel):
    role: str
    model: str
    system: str
    user: str
    response: str
    tokens: int


class Run(BaseModel):
    """One agent run; stored as the trace."""

    id: str
    task_id: str
    issue: str
    playbook_hash: str
    playbook_version: int | None = None
    status: RunStatus = RunStatus.QUEUED
    plan: str = ""
    attempts: list[Attempt] = []
    prompts: list[PromptRecord] = []
    retrieved: list[str] = []
    tokens: int = 0
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
