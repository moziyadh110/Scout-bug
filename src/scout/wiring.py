"""The only place that chooses local vs cloud adapters."""

from dataclasses import dataclass

from scout.adapters.local.fixture_github import FixtureGitHub
from scout.adapters.local.fs_store import FsStore
from scout.adapters.local.inproc_batch import InProcessBatch
from scout.adapters.local.sandbox import SubprocessSandbox
from scout.config import Settings
from scout.ports import BatchLLM, GitHub, LLMProvider, ObjectStore, Sandbox


@dataclass
class Deps:
    settings: Settings
    llm: LLMProvider
    sandbox: Sandbox
    store: ObjectStore
    github: GitHub
    batch: BatchLLM

    @property
    def mode(self) -> str:
        return "mock" if self.settings.llm_kind == "mock" else "real"


def build(settings: Settings | None = None) -> Deps:
    s = settings or Settings()
    store = FsStore(s.scout_data_dir)

    llm: LLMProvider
    if s.llm_kind == "openai":
        from scout.adapters.cloud.openai_llm import OpenAICompatLLM

        llm = OpenAICompatLLM(s.llm_base_url, s.llm_api_key)
    else:
        from scout.adapters.local.mock_llm import MockLLM

        llm = MockLLM()

    if s.ledger_kind == "solana":
        raise RuntimeError("the Solana ledger adapter is not implemented yet (Phase 4)")

    if s.github_kind == "api":
        raise RuntimeError("the live GitHub adapter is not implemented yet (needs go-ahead)")
    github = FixtureGitHub(s.fixtures_dir)

    sandbox = SubprocessSandbox(s.sandbox_timeout_seconds, _secrets(s))
    # ponytail: DigitalOcean Batch Inference adapter comes with cloud work; in-process now.
    return Deps(s, llm, sandbox, store, github, InProcessBatch(llm))


def _secrets(s: Settings) -> list[str]:
    """Values that must never appear in sandbox output (it goes back to the LLM and the UI)."""
    out = [s.llm_api_key, s.github_token, s.github_webhook_secret]
    for kp in (s.solana_escrow_keypair, s.solana_funder_keypair):
        if kp.exists():
            out.append(kp.read_text().strip())
    return out
