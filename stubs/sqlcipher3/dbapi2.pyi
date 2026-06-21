from collections.abc import Iterable, Iterator, Sequence
from typing import Any, TypeVar

from sqlcipher3 import Connection, Cursor, Row

_T_co = TypeVar("_T_co", covariant=True)

apilevel: str
threadsafety: int
paramstyle: str

PARSE_COLNAMES: int
PARSE_DECLTYPES: int

SQLITE_OK: int
SQLITE_ROW: int
SQLITE_DONE: int


class Warning(Exception): ...
class Error(Exception): ...
class InterfaceError(Error): ...
class DatabaseError(Error): ...
class DataError(DatabaseError): ...
class OperationalError(DatabaseError): ...
class IntegrityError(DatabaseError): ...
class InternalError(DatabaseError): ...
class ProgrammingError(DatabaseError): ...
class NotSupportedError(DatabaseError): ...


def connect(
    database: str,
    timeout: float = ...,
    detect_types: int = ...,
    isolation_level: str | None = ...,
    check_same_thread: bool = ...,
    factory: type[Connection] | None = ...,
    cached_statements: int = ...,
) -> Connection: ...
