"""A Flask-style app: decorated views are entrypoints, so they are not dead code."""

from .models import Admin
from .services.billing import charge
from .utils import format_money


class App:
    def __init__(self) -> None:
        self.routes: dict[str, object] = {}

    def route(self, path: str):
        def register(fn):
            self.routes[path] = fn
            return fn

        return register


app = App()


@app.route("/checkout")
def checkout(user_id: int) -> str:
    admin = Admin("root")
    return charge(admin, user_id)


@app.route("/")
def index() -> str:
    return format_money(0)
