from __future__ import annotations


class CacheKeyMismatchError(Exception):
    """Raised when the SQLCipher encryption key does not match the cache database.

    This typically happens when:
    - The user's IMAP password was changed
    - The server was restarted and the in-memory key was lost
    - The API token's DEK does not match the key used to create the cache DB
    """
