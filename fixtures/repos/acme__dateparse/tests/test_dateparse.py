from datetime import date

from dateparse import parse


def test_iso_date():
    assert parse(" 2024-02-01 ") == date(2024, 2, 1)
