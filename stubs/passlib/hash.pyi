"""Minimal type stub for the dynamic passlib.hash registry proxy.

The real ``passlib.hash`` module populates scheme handlers (``sha256_crypt``,
...) dynamically via ``_PasslibRegistryProxy``, which static type checkers
cannot see. Only the handlers used by this repo are declared here.
"""

from typing import Any

sha256_crypt: Any

__all__ = ["sha256_crypt"]
