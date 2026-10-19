"""Sample shop package used as Faultline's ground-truth fixture.

Every construct in here exists on purpose. See tests/unit/test_fixture_project.py
for the exact answers each analysis is expected to give.
"""

from .models import Admin, User
from .services.billing import charge

__all__ = ["Admin", "User", "charge"]
