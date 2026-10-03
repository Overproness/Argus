import hmac
import secrets

from .. import db


async def login(email: str, password: str) -> str | None:
    user = await db.get_user(email)
    if user is None:
        return None
    digest = db.hash_password(password, user["salt"])
    if not hmac.compare_digest(digest, user["pw_hash"]):
        return None
    return secrets.token_hex(16)
