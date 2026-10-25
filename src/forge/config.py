from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["planner", "coder", "judge", "reasoner"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    forge_env: Literal["local", "cloud"] = "local"
    # Per-port overrides; default follows FORGE_ENV.
    forge_llm: Literal["mock", "openai"] | None = None
    forge_ledger: Literal["sqlite", "solana"] | None = None

    forge_data_dir: Path = Path(".forge-data")
    benchmarks_dir: Path = Path("benchmarks")
    playbook_seed: Path = Path("playbooks/v1.json")

    llm_base_url: str = "https://inference.do-ai.run/v1"
    llm_api_key: str = ""
    model_planner: str = "mock"
    model_coder: str = "mock"
    model_judge: str = "mock"
    model_reasoner: str = "mock"
    llm_workers: int = 4

    bounty_cap_lamports: int = 100_000_000
    bounty_deadline_seconds: int = 600
    sandbox_timeout_seconds: int = 20

    solana_rpc_url: str = "https://api.devnet.solana.com"
    solana_escrow_keypair: Path = Path("keys/escrow.keypair.json")
    solana_treasury_keypair: Path = Path("keys/treasury.keypair.json")
    solana_payer_keypair: Path = Path("keys/payer.keypair.json")

    # Pasted bugs run user code; the local sandbox is best-effort, so default off in cloud.
    allow_user_bugs: bool | None = None

    @property
    def user_bugs_enabled(self) -> bool:
        if self.allow_user_bugs is not None:
            return self.allow_user_bugs
        return self.forge_env == "local"

    @property
    def llm_kind(self) -> str:
        return self.forge_llm or ("openai" if self.forge_env == "cloud" else "mock")

    @property
    def ledger_kind(self) -> str:
        return self.forge_ledger or ("solana" if self.forge_env == "cloud" else "sqlite")

    def model_for(self, role: Role) -> str:
        return str(getattr(self, f"model_{role}"))
