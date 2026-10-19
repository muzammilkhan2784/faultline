from shop.models import Admin


def test_admin_promote_describes():
    assert Admin("root").promote() == "admin:root"
