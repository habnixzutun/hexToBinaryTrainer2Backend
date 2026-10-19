"""Legacy User model - DEPRECATED.

This file is kept for backward compatibility during migration.
The new User model is in src/models/auth.py.

All new code should use the User model from src/models/auth.py.
"""

# Re-export from auth module for backward compatibility
from src.models.auth import User

__all__ = ["User"]
