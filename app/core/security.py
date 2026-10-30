import hashlib, secrets
from fastapi import Request


def hash_password(p: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 100_000).hex()


def verify_password(p: str, stored: str) -> bool:
    try:
        return secrets.compare_digest(hash_password(p, stored.split("$")[0]), stored)
    except Exception:
        return False


async def auth_gate(req: Request, call_next):
    """Authentication removed as requested: open access to all operations & endpoints."""
    return await call_next(req)
