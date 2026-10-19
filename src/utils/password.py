import re

import bcrypt

PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Z])(?=.*\d).{8,128}$")
BCRYPT_COST = 12


def validate_password_strength(password: str) -> None:
    if not PASSWORD_PATTERN.match(password):
        raise ValueError(
            "Password must be 8–128 characters and contain at least one uppercase letter and one digit."
        )


def hash_password(plain: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_COST)
    return bcrypt.hashpw(plain.encode(), salt).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())
