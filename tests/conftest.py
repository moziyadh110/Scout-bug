from pathlib import Path

import pytest

from forge.config import Settings
from forge.wiring import Deps, build

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        forge_env="local",
        forge_llm="mock",
        forge_ledger="sqlite",
        forge_data_dir=tmp_path / "data",
        benchmarks_dir=ROOT / "benchmarks",
        playbook_seed=ROOT / "playbooks" / "v1.json",
        sandbox_timeout_seconds=5,
    )


@pytest.fixture
def deps(settings: Settings) -> Deps:
    return build(settings)
