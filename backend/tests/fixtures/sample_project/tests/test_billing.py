from shop.models import User
from shop.services.billing import charge


def test_charge_applies_discount():
    user = User("ada")
    assert charge(user, 1000, 10) == "$9.0"


def test_charge_without_discount():
    assert charge(User("bob"), 500) == "$5.0"
