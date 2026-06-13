import base64
from cryptography.fernet import Fernet

from app.modules.mail.services.crypto import derive_key


def _fernet_from_secret(secret, salt):
    key = derive_key(secret, salt)
    return _fernet_from_key(key)


def _fernet_from_key(derived_key_hex):
    key_bytes = bytes.fromhex(derived_key_hex)
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt_secret(secret_value, secret, salt):
    f = _fernet_from_secret(secret, salt)
    return f.encrypt(secret_value.encode())


def decrypt_secret(encrypted_value, secret, salt):
    f = _fernet_from_secret(secret, salt)
    return f.decrypt(encrypted_value).decode()


def encrypt_with_key(secret_value, derived_key_hex):
    f = _fernet_from_key(derived_key_hex)
    return f.encrypt(secret_value.encode())


def decrypt_with_key(encrypted_value, derived_key_hex):
    f = _fernet_from_key(derived_key_hex)
    return f.decrypt(encrypted_value).decode()
