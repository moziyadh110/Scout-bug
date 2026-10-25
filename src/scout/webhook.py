"""GitHub webhook intake. A valid signature makes an event a trigger, never a source of truth:
the bounty code re-fetches the PR before any check."""

import hashlib
import hmac
import json
from typing import Any


def signature_ok(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def merged_pr(event: str | None, body: bytes) -> tuple[str, int] | None:
    """(repo, PR number) for a 'pull_request closed + merged' event, else None."""
    if event != "pull_request":
        return None
    payload: dict[str, Any] = json.loads(body)
    pr = payload.get("pull_request") or {}
    if payload.get("action") != "closed" or not pr.get("merged"):
        return None
    return str(payload["repository"]["full_name"]), int(pr["number"])
