# routes/__init__.py

from .auth import register_auth_routes
from .courier import register_courier_routes
from .operator import register_operator_routes


def init_routes(app):
    """
    Центральная точка регистрации всех роутов.
    """
    register_auth_routes(app)
    register_courier_routes(app)
    register_operator_routes(app)
