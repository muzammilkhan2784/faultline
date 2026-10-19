from shop.web import checkout, index


def test_checkout_returns_receipt():
    assert checkout(1).startswith("$")


def test_index_is_free():
    assert index() == "$0.0"
