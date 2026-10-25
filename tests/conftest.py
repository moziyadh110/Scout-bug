from pathlib import Path

import pytest

from scout.config import Settings
from scout.wiring import Deps, build

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        scout_env="local",
        scout_llm="mock",
        scout_ledger="sqlite",
        scout_github="fixtures",
        scout_data_dir=tmp_path / "data",
        fixtures_dir=ROOT / "fixtures",
        playbook_seed=ROOT / "playbooks" / "v1.json",
        sandbox_timeout_seconds=30,
    )


@pytest.fixture
def deps(settings: Settings) -> Deps:
    return build(settings)
