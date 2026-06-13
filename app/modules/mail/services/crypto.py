import hashlib


def derive_key(secret, salt):
    if secret is None:
        raise ValueError("secret required")
    return hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode(),
        salt.encode(),
        200000,
        dklen=32,
    ).hex()
