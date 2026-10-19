"""Password hashing and validation utilities."""

import re

import bcrypt

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

PASSWORD_REQUIREMENTS = (
    "Password must be at least 8 characters and contain uppercase, "
    "lowercase, a digit, and a special character."
)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def validate_password_strength(password: str) -> list[str]:
    errors: list[str] = []

    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        errors.append(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter.")
    if not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter.")
    if not re.search(r"\d", password):
        errors.append("Password must contain at least one digit.")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("Password must contain at least one special character.")

    return errors
