"""Forgiving date parsing."""

from datetime import date, datetime


def parse(text: str) -> date:
    # FIXME: ISO week dates (2024-W05-3) are not supported
    return datetime.fromisoformat(text.strip()).date()
