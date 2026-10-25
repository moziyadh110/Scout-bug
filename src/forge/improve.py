"""Benchmark evaluation, versioned playbook registry, nightly propose->probe->compose."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import ValidationError

from forge.agent import Task, load_tasks, memory_example
from forge.models import EvalResult, Playbook, PlaybookVersion, Run, RunStatus, now
from forge.wiring import Deps

EDITABLE = {"system_prompts", "strategy", "max_retries", "use_memory", "memory_k"}


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def playbook_hash(pb: Playbook) -> str:
    return sha256(canonical(pb.model_dump(mode="json")))


def version_hash(pb: Playbook, memory_hash: str, ev: EvalResult) -> str:
    return sha256(
        canonical(
            {
                "playbook": pb.model_dump(mode="json"),
                "memory": memory_hash,
                "eval": ev.model_dump(mode="json"),
            }
        )
    )


def _rate(per_task: dict[str, bool], tasks: list[Task], split: str) -> float:
    names = [t.name for t in tasks if t.split == split]
    return round(sum(per_task[n] for n in names) / len(names), 4) if names else 0.0


def evaluate(
    deps: Deps, pb: Playbook, tasks: list[Task], version: int | None = None
) -> tuple[EvalResult, list[Run]]:
    ph, agent, t0 = playbook_hash(pb), deps.agent, time.perf_counter()
    with ThreadPoolExecutor(deps.settings.llm_workers) as ex:
        runs = list(ex.map(lambda t: agent.solve(t, pb, ph, version), tasks))
    for r in runs:
        deps.store.put_json(f"traces/{r.id}.json", r.model_dump(mode="json"))
    per = {r.task_id: r.status == RunStatus.PASSED for r in runs}
    ev = EvalResult(
        playbook_hash=ph,
        mode=deps.mode,  # type: ignore[arg-type]
        train_pass_rate=_rate(per, tasks, "train"),
        holdout_pass_rate=_rate(per, tasks, "holdout"),
        per_task=per,
        total_tokens=sum(r.tokens for r in runs),
        wall_seconds=round(time.perf_counter() - t0, 2),
    )
    return ev, runs


# --- registry ---------------------------------------------------------------


def versions(deps: Deps) -> list[PlaybookVersion]:
    return [
        PlaybookVersion.model_validate(deps.store.get_json(k)) for k in deps.store.list("versions")
    ]


def latest(deps: Deps) -> PlaybookVersion | None:
    vs = versions(deps)
    return vs[-1] if vs else None


def commit(
    deps: Deps,
    pb: Playbook,
    parent: int | None,
    memory_hash: str,
    ev: EvalResult,
    runs: list[Run],
    rationale: str,
) -> PlaybookVersion:
    n = (parent or 0) + 1
    v = PlaybookVersion(
        version=n,
        parent=parent,
        playbook=pb,
        memory_snapshot_hash=memory_hash,
        eval=ev,
        hash=version_hash(pb, memory_hash, ev),
        rationale=rationale,
    )
    v.ledger_ref = deps.ledger.record(f"version:{v.hash}", f"forge:playbook:v{n}:{v.hash}")
    deps.store.put_json(f"versions/v{n:04d}.json", v.model_dump(mode="json"))
    deps.store.put_json(f"evals/v{n:04d}.json", [r.id for r in runs])
    for r in runs:  # successful fixes become memory for future versions
        if r.status == RunStatus.PASSED:
            deps.memory.add(
                r.id, r.task_id, r.issue + "\n" + r.attempts[-1].diff, memory_example(r)
            )
    return v


def ensure_seed(deps: Deps) -> PlaybookVersion:
    cur = latest(deps)
    if cur:
        return cur
    pb = Playbook.model_validate_json(deps.settings.playbook_seed.read_text())
    mem = deps.memory.snapshot_hash()
    ev, runs = evaluate(deps, pb, load_tasks(deps.settings.benchmarks_dir), version=1)
    return commit(deps, pb, None, mem, ev, runs, "seed playbook")


def verify(deps: Deps, n: int) -> dict[str, Any]:
    """Recompute a version's hash from the store and compare with the ledger record."""
    data = deps.store.get_json(f"versions/v{n:04d}.json")
    if data is None:
        return {"ok": False, "error": f"no version {n}"}
    v = PlaybookVersion.model_validate(data)
    recomputed = version_hash(v.playbook, v.memory_snapshot_hash, v.eval)
    memo = deps.ledger.get_memo(v.ledger_ref) if v.ledger_ref else None
    expected = f"forge:playbook:v{n}:{recomputed}"
    return {
        "ok": recomputed == v.hash and memo == expected,
        "version": n,
        "stored_hash": v.hash,
        "recomputed_hash": recomputed,
        "ledger_memo": memo,
        "ledger_ref": v.ledger_ref,
        "explorer_url": deps.ledger.explorer_url(v.ledger_ref) if v.ledger_ref else None,
    }


# --- nightly ----------------------------------------------------------------


def _failures(deps: Deps, v: PlaybookVersion, train: set[str]) -> list[Run]:
    ids = deps.store.get_json(f"evals/v{v.version:04d}.json") or []
    runs = [Run.model_validate(deps.store.get_json(f"traces/{i}.json")) for i in ids]
    return [r for r in runs if r.task_id in train and r.status != RunStatus.PASSED]


def _proposals(text: str, cur: Playbook) -> list[tuple[dict[str, Any], Playbook, str]]:
    try:
        items = json.loads(text[text.index("[") : text.rindex("]") + 1])
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        edit = {k: v for k, v in item.items() if k in EDITABLE}
        try:
            cand = Playbook.model_validate({**cur.model_dump(), **edit})
        except ValidationError:
            continue
        if edit and cand != cur:
            out.append((edit, cand, str(item.get("rationale", ""))))
    return out


def nightly(deps: Deps, k: int = 3) -> dict[str, Any]:
    cur = ensure_seed(deps)
    tasks = load_tasks(deps.settings.benchmarks_dir)
    train = [t for t in tasks if t.split == "train"]
    report: dict[str, Any] = {
        "at": now().isoformat(),
        "from_version": cur.version,
        "mode": deps.mode,
        "candidates": [],
        "accepted": None,
    }

    fails = _failures(deps, cur, {t.name for t in train})
    if not fails:
        report["note"] = "no train failures to learn from"
        return _save_report(deps, report)

    failures = "\n\n".join(
        f"### {r.task_id}\n{r.issue.splitlines()[0]}\nLast test output:\n"
        f"{r.attempts[-1].test_output[-600:] if r.attempts else '(no attempt)'}"
        for r in fails
    )
    prompt = (
        f"## TASK: IMPROVE\n## CURRENT PLAYBOOK\n{cur.playbook.model_dump_json(indent=2)}\n"
        f"## FAILURES\n{failures}\n## SCORE\ntrain pass rate {cur.eval.train_pass_rate}\n"
        f"Propose up to {k} independent edits to the playbook as a JSON list. Each item "
        "holds only the fields to change (system_prompts, strategy, max_retries 0-4, "
        "use_memory, memory_k 1-5) plus a 'rationale'. Return only JSON."
    )
    reply = deps.llm.complete(deps.settings.model_for("reasoner"), "", prompt).text
    proposals = _proposals(reply, cur.playbook)[:k]

    mem = deps.memory.snapshot_hash()
    winners = []
    for edit, cand, why in proposals:  # probe
        ev, _ = evaluate(deps, cand, train)
        row = {
            "edit": edit,
            "rationale": why,
            "train_pass_rate": ev.train_pass_rate,
            "improved": ev.train_pass_rate > cur.eval.train_pass_rate,
        }
        report["candidates"].append(row)
        if row["improved"]:
            winners.append((ev.train_pass_rate, edit, why))

    if not winners:
        report["note"] = "no candidate beat the current playbook on train"
        return _save_report(deps, report)

    merged = cur.playbook.model_dump()  # compose: on a field clash the best-scoring edit wins
    for _, edit, _ in sorted(winners, key=lambda w: w[0]):
        merged.update(edit)
    composed = Playbook.model_validate(merged)
    ev, runs = evaluate(deps, composed, tasks, version=cur.version + 1)
    old = cur.eval
    better = (
        ev.train_pass_rate >= old.train_pass_rate
        and ev.holdout_pass_rate >= old.holdout_pass_rate
        and (ev.train_pass_rate, ev.holdout_pass_rate)
        != (old.train_pass_rate, old.holdout_pass_rate)
    )
    report["composed"] = {
        "playbook": composed.model_dump(),
        "train_pass_rate": ev.train_pass_rate,
        "holdout_pass_rate": ev.holdout_pass_rate,
    }
    if better:
        rationale = "; ".join(w[2] for w in winners)
        v = commit(deps, composed, cur.version, mem, ev, runs, rationale)
        report["accepted"] = v.version
    else:
        report["note"] = "composed playbook regressed or tied on train/holdout; rejected"
    return _save_report(deps, report)


def _save_report(deps: Deps, report: dict[str, Any]) -> dict[str, Any]:
    deps.store.put_json(f"nightly/{report['at'].replace(':', '-')}.json", report)
    return report
