"""Tests for agent-ledger currency support."""

import pytest
from datetime import datetime, timezone, timedelta

from agent_ledger.models import ExchangeRate
from agent_ledger.currency import CurrencyConverter
from agent_ledger.exceptions import ExchangeRateNotFoundError


class TestCurrencyConverter:
    """Test CurrencyConverter class."""

    def test_identity_conversion(self):
        converter = CurrencyConverter()
        result = converter.convert(100.0, "USD", "USD")
        assert result == 100.0

    def test_direct_rate(self):
        converter = CurrencyConverter([
            ExchangeRate(from_currency="USD", to_currency="EUR", rate=0.85),
        ])
        result = converter.convert(100.0, "USD", "EUR")
        assert result == 85.0

    def test_inverse_rate(self):
        converter = CurrencyConverter([
            ExchangeRate(from_currency="EUR", to_currency="USD", rate=1.1765),
        ])
        result = converter.convert(100.0, "USD", "EUR")
        assert round(result, 2) == 85.0  # 100 / 1.1765

    def test_rate_not_found(self):
        converter = CurrencyConverter()
        with pytest.raises(ExchangeRateNotFoundError):
            converter.convert(100.0, "USD", "JPY")

    def test_most_recent_rate(self):
        earlier = ExchangeRate(
            from_currency="USD", to_currency="EUR", rate=0.80,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        later = ExchangeRate(
            from_currency="USD", to_currency="EUR", rate=0.85,
            timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        converter = CurrencyConverter([earlier, later])
        result = converter.convert(100.0, "USD", "EUR")
        assert result == 85.0  # Uses later rate

    def test_rate_as_of(self):
        earlier = ExchangeRate(
            from_currency="USD", to_currency="EUR", rate=0.80,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        later = ExchangeRate(
            from_currency="USD", to_currency="EUR", rate=0.85,
            timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        converter = CurrencyConverter([earlier, later])
        result = converter.convert(100.0, "USD", "EUR",
                                    as_of=datetime(2024, 3, 1, tzinfo=timezone.utc))
        assert result == 80.0  # Uses earlier rate

    def test_add_rate(self):
        converter = CurrencyConverter()
        converter.add_rate(ExchangeRate(
            from_currency="USD", to_currency="GBP", rate=0.75,
        ))
        result = converter.convert(100.0, "USD", "GBP")
        assert result == 75.0

    def test_list_rates(self):
        rates = [
            ExchangeRate(from_currency="USD", to_currency="EUR", rate=0.85),
            ExchangeRate(from_currency="USD", to_currency="GBP", rate=0.75),
        ]
        converter = CurrencyConverter(rates)
        assert len(converter.list_rates()) == 2

    def test_remove_rate(self):
        converter = CurrencyConverter([
            ExchangeRate(from_currency="USD", to_currency="EUR", rate=0.85),
        ])
        converter.remove_rate(0)
        assert len(converter.list_rates()) == 0

    def test_rounding(self):
        converter = CurrencyConverter([
            ExchangeRate(from_currency="USD", to_currency="JPY", rate=149.50),
        ])
        result = converter.convert(100.0, "USD", "JPY")
        assert result == 14950.0
