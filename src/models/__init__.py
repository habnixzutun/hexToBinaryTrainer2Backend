# src/models/__init__.py
from .user import User
from .ip_address import IpAddress

# Damit kannst du später einfach "from src.models import User, IpAddress" schreiben
__all__ = ["User", "IpAddress"]