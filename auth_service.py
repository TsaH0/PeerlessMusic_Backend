"""
Authentication service with JWT tokens for simple identity-based auth.
Users can optionally create an "Identity" with username/password.
"""

import os
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional
import jwt

# JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 90  # 3 months


def hash_password(password: str) -> str:
    """Hash a password using SHA-256 with salt."""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against its hash."""
    try:
        salt, hashed = stored_hash.split(":")
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() == hashed
    except ValueError:
        return False


def create_token(user_id: str, username: str) -> str:
    """Create a JWT token for a user."""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def generate_user_id() -> str:
    """Generate a unique user ID."""
    return secrets.token_hex(8)
