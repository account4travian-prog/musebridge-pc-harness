from legacy import legacy_label


def test_legacy_label():
    assert legacy_label("x") == "item:x"
