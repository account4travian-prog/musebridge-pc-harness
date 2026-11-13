from counter import values_up_to


def test_values_up_to_excludes_limit():
    assert values_up_to(4) == [0, 1, 2, 3]


def test_zero_has_no_values():
    assert values_up_to(0) == []
