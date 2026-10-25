from tinycsv import read_rows, write_rows


def test_read_rows():
    assert read_rows("a,b\n1,2") == [["a", "b"], ["1", "2"]]


def test_round_trip():
    rows = [["x", "y"], ["1", "2"]]
    assert read_rows(write_rows(rows)) == rows
