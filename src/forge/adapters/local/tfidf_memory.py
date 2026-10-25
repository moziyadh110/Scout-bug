import hashlib
import json
import math
import re
from collections import Counter

from forge.ports import ObjectStore

KEY = "memory/docs.json"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z_][a-z0-9_]+", text.lower())


class TfidfMemory:
    """Past successful fixes, retrieved by TF-IDF cosine similarity. Persisted in the store."""

    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    def _docs(self) -> list[dict[str, str]]:
        return self.store.get_json(KEY) or []

    def add(self, doc_id: str, task_id: str, text: str, example: str) -> None:
        docs = [d for d in self._docs() if d["id"] != doc_id]
        docs.append({"id": doc_id, "task": task_id, "text": text, "example": example})
        self.store.put_json(KEY, docs)

    def search(self, query: str, k: int, exclude_task: str | None = None) -> list[str]:
        docs = [d for d in self._docs() if d["task"] != exclude_task]
        if not docs:
            return []
        tfs = [Counter(_tokens(d["text"])) for d in docs]
        df = Counter(t for tf in tfs for t in tf)
        idf = {t: math.log((1 + len(docs)) / (1 + n)) + 1 for t, n in df.items()}

        def vec(tf: Counter[str]) -> dict[str, float]:
            return {t: c * idf.get(t, 0.0) for t, c in tf.items()}

        def cos(a: dict[str, float], b: dict[str, float]) -> float:
            dot = sum(v * b.get(t, 0.0) for t, v in a.items())
            na = math.sqrt(sum(v * v for v in a.values()))
            nb = math.sqrt(sum(v * v for v in b.values()))
            return dot / (na * nb) if na and nb else 0.0

        q = vec(Counter(_tokens(query)))
        ranked = sorted(zip(docs, tfs, strict=True), key=lambda p: cos(q, vec(p[1])), reverse=True)
        return [d["example"] for d, _ in ranked[:k]]

    def snapshot_hash(self) -> str:
        docs = sorted(self._docs(), key=lambda d: d["id"])
        return hashlib.sha256(json.dumps(docs, sort_keys=True).encode()).hexdigest()
