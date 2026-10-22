"""Exact fixed-point money and quantity helpers.

Prices are stored as integers in units of 1e-4 USD (Kalshi's tick precision); quantities as
integers in 1e-8 units (satoshi precision, which also covers Kalshi's fixed-point counts).
Floats never touch PnL. ``Decimal`` is used only to parse/format at API boundaries.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

PRICE_SCALE = 10_000  # 1 unit = $0.0001
QTY_SCALE = 100_000_000  # 1 unit = 1e-8


class FixedPointError(ValueError):
    pass


def _to_scaled(value: str | int | Decimal, scale: int, *, exact: bool) -> int:
    try:
        d = Decimal(value) * scale
    except InvalidOperation as e:
        raise FixedPointError(f"not a number: {value!r}") from e
    if not d.is_finite():
        raise FixedPointError(f"not finite: {value!r}")
    rounded = d.to_integral_value(rounding=ROUND_HALF_EVEN)
    if exact and rounded != d:
        raise FixedPointError(f"{value!r} has more precision than 1/{scale}")
    return int(rounded)


def parse_price(value: str | int | Decimal, *, exact: bool = True) -> int:
    """Parse a dollar amount such as ``"8.1660"`` into 1e-4 USD units."""
    return _to_scaled(value, PRICE_SCALE, exact=exact)


def parse_qty(value: str | int | Decimal, *, exact: bool = True) -> int:
    """Parse a quantity such as ``"0.0001"`` or ``"146.00"`` into 1e-8 units."""
    return _to_scaled(value, QTY_SCALE, exact=exact)


def format_price(units: int) -> str:
    return str((Decimal(units) / PRICE_SCALE).quantize(Decimal("0.0001")))


def format_qty(units: int) -> str:
    return str((Decimal(units) / QTY_SCALE).normalize())
