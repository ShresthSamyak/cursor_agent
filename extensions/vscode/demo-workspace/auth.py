"""OAuth login stub for the Trail Act 3 demo (see README.md in this folder)."""

import hmac
import hashlib

USERS = {"ada@example.com": {"email": "ada@example.com", "password_hash": hashlib.sha256(b"hunter2").hexdigest()}}


class db:
    @staticmethod
    def get_user(email):
        return USERS.get(email)


def hash_pw(password):
    return hashlib.sha256(password.encode()).hexdigest()


def login(email, password):
    user = db.get_user(email)
    if hmac.compare_digest(hash_pw(password), user["password_hash"]):
        return user["email"]
    return None
