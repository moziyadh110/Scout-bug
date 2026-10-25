import logging
from concurrent.futures import ThreadPoolExecutor

from scout.ports import BatchRequest, Completion, LLMProvider

log = logging.getLogger("scout")


class InProcessBatch:
    """Batch port over a plain LLM, a few requests at a time."""

    def __init__(self, llm: LLMProvider, workers: int = 4) -> None:
        self.llm, self.workers = llm, workers

    def run(self, requests: list[BatchRequest]) -> dict[str, Completion]:
        def one(r: BatchRequest) -> tuple[str, Completion | None]:
            try:
                return r.id, self.llm.complete(r.model, r.system, r.user)
            except Exception:
                log.exception("batch request %s failed", r.id)
                return r.id, None

        with ThreadPoolExecutor(self.workers) as ex:
            return {rid: c for rid, c in ex.map(one, requests) if c is not None}
