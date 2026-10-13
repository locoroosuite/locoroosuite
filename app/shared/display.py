"""Per-user display preferences shared across modules (HLD UX8c)."""

from __future__ import annotations

TEXT_SCALE_CHOICES: tuple[str, ...] = ("m", "l", "xl")
TEXT_SCALE_VALUES: dict[str, float] = {"m": 1.0, "l": 1.125, "xl": 1.25}


def text_scale_factor(value: str | None) -> float:
    """Return the font-size multiplier for a stored ``text_scale`` value."""
    return TEXT_SCALE_VALUES.get(value or "m", 1.0)


def format_text_scale(value: str | None) -> str:
    """Format the multiplier for CSS (``1``, ``1.125``, ``1.25``)."""
    return f"{text_scale_factor(value):g}"
