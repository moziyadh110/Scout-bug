"""The fixed skill vocabulary shared by issue classification and contributor profiling."""

import re

KEYWORDS: dict[str, list[str]] = {
    "parsing": ["parse", "parser", "parsing", "delimiter", "regex", "tokeniz"],
    "csv": ["csv", "tsv"],
    "datetime": ["date", "datetime", "timezone", "iso 8601", "week"],
    "testing": ["test", "pytest", "flaky", "coverage", "snapshot"],
    "typing": ["type hint", "typing", "mypy", "annotation", "typed"],
    "docs": ["document", "readme", "docstring", "markdown"],
    "cli": ["cli", "command line", "argparse"],
    "packaging": ["packaging", "pyproject", "wheel", "release"],
    "refactoring": ["refactor", "restructure", "unify", "clean up"],
    "http": ["http", "rest api", "requests"],
    "config": ["config", "settings"],
}
VOCABULARY = set(KEYWORDS)


def skills_in(text: str) -> list[str]:
    t = text.lower()
    return sorted(s for s, words in KEYWORDS.items() if any(re.search(rf"\b{w}", t) for w in words))
