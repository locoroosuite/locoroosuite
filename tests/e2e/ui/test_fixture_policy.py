"""Guards the UI e2e SSE-block policy (e2e strategy P2).

The fixtures in tests/e2e/ui/conftest.py must block /events/stream unless
the test opts in via @pytest.mark.allow_sse. If this policy silently breaks
(e.g. the glob no longer matches the endpoint), the DOM-replacement race
class returns to every UI test. This is a pure unit test: no browser, no
live services.
"""

from collections.abc import Callable
from typing import Any, cast

import pytest

from tests.e2e.ui.conftest import _apply_sse_policy


class _Node:
    def __init__(self, has_marker: bool):
        self._has_marker = has_marker

    def get_closest_marker(self, name: str) -> Any:
        return object() if self._has_marker else None


class _Request:
    def __init__(self, node: _Node):
        self.node = node


class _Route:
    def __init__(self):
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class _Context:
    def __init__(self):
        self.routes: list[tuple[str, Callable[[_Route], None]]] = []

    def route(self, pattern: str, handler: Callable[[_Route], None]) -> None:
        self.routes.append((pattern, handler))


def _ctx(with_marker: bool) -> _Context:
    context = _Context()
    request = cast(pytest.FixtureRequest, _Request(_Node(with_marker)))
    _apply_sse_policy(context, request)
    return context


def test_sse_blocked_by_default():
    context = _ctx(with_marker=False)
    assert [pattern for pattern, _ in context.routes] == ["**/events/stream"]
    handler = context.routes[0][1]
    route = _Route()
    handler(route)
    assert route.aborted


def test_allow_sse_marker_removes_block():
    assert _ctx(with_marker=True).routes == []
