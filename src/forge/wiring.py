"""The only place that chooses local vs cloud adapters."""

from dataclasses import dataclass

from forge.adapters.local.fs_store import FsStore
from forge.adapters.local.sandbox import SubprocessSandbox
from forge.adapters.local.sqlite_ledger import SqliteLedger
from forge.adapters.local.tfidf_memory import TfidfMemory
from forge.agent import Agent
from forge.config import Settings
from forge.ports import Ledger, LLMProvider, Memory, ObjectStore, Sandbox


@dataclass
class Deps:
    settings: Settings
    llm: LLMProvider
    sandbox: Sandbox
    store: ObjectStore
    memory: Memory
    ledger: Ledger

    @property
    def agent(self) -> Agent:
        return Agent(self.llm, self.sandbox, self.memory, self.settings)

    @property
    def mode(self) -> str:
        return "mock" if self.settings.llm_kind == "mock" else "real"


def build(settings: Settings | None = None) -> Deps:
    s = settings or Settings()
    store = FsStore(s.forge_data_dir)

    llm: LLMProvider
    if s.llm_kind == "openai":
        from forge.adapters.cloud.openai_llm import OpenAICompatLLM

        llm = OpenAICompatLLM(s.llm_base_url, s.llm_api_key)
    else:
        from forge.adapters.local.mock_llm import MockLLM

        llm = MockLLM(s.benchmarks_dir)

    ledger: Ledger
    if s.ledger_kind == "solana":
        from forge.adapters.cloud.solana_ledger import SolanaLedger

        ledger = SolanaLedger(s)
    else:
        ledger = SqliteLedger(s.forge_data_dir / "ledger.sqlite3")

    sandbox = SubprocessSandbox(s.sandbox_timeout_seconds, _secrets(s))
    return Deps(s, llm, sandbox, store, TfidfMemory(store), ledger)


def _secrets(s: Settings) -> list[str]:
    """Values that must never appear in sandbox output (it goes back to the LLM and the UI)."""
    out = [s.llm_api_key]
    for kp in (s.solana_escrow_keypair, s.solana_treasury_keypair, s.solana_payer_keypair):
        if kp.exists():
            out.append(kp.read_text().strip())
    return out
