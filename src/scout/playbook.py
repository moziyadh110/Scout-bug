from scout.models import Playbook, PlaybookVersion
from scout.wiring import Deps


def versions(deps: Deps) -> list[PlaybookVersion]:
    return [
        PlaybookVersion.model_validate(deps.store.get_json(k)) for k in deps.store.list("versions")
    ]


def current_playbook(deps: Deps) -> tuple[Playbook, int | None]:
    """The latest accepted version, or the seed before the first nightly run."""
    vs = versions(deps)
    if vs:
        return vs[-1].playbook, vs[-1].version
    return Playbook.model_validate_json(deps.settings.playbook_seed.read_text()), None
