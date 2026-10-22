from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from basis.core.fixed import (
    FixedPointError,
    format_price,
    format_qty,
    parse_price,
    parse_qty,
)


def test_parse_kalshi_prices() -> None:
    assert parse_price("8.1660") == 81_660
    assert parse_price("0.0001") == 1
    assert parse_qty("146.00") == 146 * 10**8
    assert parse_qty("0.0001") == 10_000


def test_exact_rejects_excess_precision() -> None:
    with pytest.raises(FixedPointError):
        parse_price("1.00005")
    assert parse_price("1.00005", exact=False) == 10_000  # half-even


@pytest.mark.parametrize("bad", ["abc", "NaN", "Infinity", ""])
def test_rejects_non_numbers(bad: str) -> None:
    with pytest.raises(FixedPointError):
        parse_price(bad)


@given(st.integers(min_value=-(10**15), max_value=10**15))
def test_price_roundtrip(units: int) -> None:
    assert parse_price(format_price(units)) == units


@given(st.integers(min_value=0, max_value=10**18))
def test_qty_roundtrip(units: int) -> None:
    assert parse_qty(format_qty(units)) == units
    assert Decimal(format_qty(units)) * 10**8 == units
