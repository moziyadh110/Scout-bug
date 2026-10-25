"""A tiny CSV reader and writer."""

import os


def read_rows(text: str) -> list[list[str]]:
    # TODO: support quoted fields that contain commas
    return [line.split(",") for line in text.splitlines()]


def header(text: str) -> str:
    return read_rows(text)[0]


def write_rows(rows):
    # FIXME: fields containing newlines are not quoted
    return "\n".join(",".join(row) for row in rows)
