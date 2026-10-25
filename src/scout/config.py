from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["planner", "classifier", "reasoner", "coder"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    scout_env: Literal["local", "cloud"] = "local"
    # Per-port overrides; default follows SCOUT_ENV.
    scout_llm: Literal["mock", "openai"] | None = None
    scout_ledger: Literal["sqlite", "solana"] | None = None
    scout_github: Literal["fixtures", "api"] | None = None

    scout_data_dir: Path = Path(".scout-data")
    fixtures_dir: Path = Path("fixtures")
    playbook_seed: Path = Path("playbooks/v1.json")

    # Default cloud provider: DigitalOcean Gradient serverless inference (OpenAI-compatible).
    llm_base_url: str = "https://inference.do-ai.run/v1"
    llm_api_key: str = ""
    model_planner: str = "mock"
    model_classifier: str = "mock"
    model_reasoner: str = "mock"
    model_coder: str = "mock"

    github_token: str = ""
    github_webhook_secret: str = ""

    # Nightly index
    discover_min_stars: int = 200
    discover_max_stars: int = 20_000
    discover_pushed_within_days: int = 90
    discover_limit: int = 8  # repos fetched per search on the live API
    backtest_per_repo: int = 15
    max_tarball_mb: int = 50
    min_health: float = 0.5
    gap_top_n_repos: int = 5
    # Matching
    verify_top_k: int = 3
    # Bounties (test USDC, 6 decimals; amounts are base units)
    usdc_decimals: int = 6
    bounty_cap_units: int = 100_000_000
    bounty_deadline_days: int = 30
    dispute_window_seconds: int = 7 * 24 * 3600
    min_diff_lines: int = 3
    # Self-improvement
    min_gain: float = 0.05
    proposals_k: int = 3

    sandbox_timeout_seconds: int = 60
    enable_starter_patch: bool = False

    solana_rpc_url: str = "https://api.devnet.solana.com"
    solana_escrow_keypair: Path = Path("keys/escrow.keypair.json")
    solana_funder_keypair: Path = Path("keys/funder.keypair.json")

    @property
    def llm_kind(self) -> str:
        return self.scout_llm or ("openai" if self.scout_env == "cloud" else "mock")

    @property
    def ledger_kind(self) -> str:
        return self.scout_ledger or ("solana" if self.scout_env == "cloud" else "sqlite")

    @property
    def github_kind(self) -> str:
        return self.scout_github or ("api" if self.scout_env == "cloud" else "fixtures")

    def model_for(self, role: Role) -> str:
        return str(getattr(self, f"model_{role}"))
