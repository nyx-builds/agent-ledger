"""Multi-currency support for agent-ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import ExchangeRate
from .exceptions import ExchangeRateNotFoundError


class CurrencyConverter:
    """Handles currency conversion using stored exchange rates."""

    def __init__(self, rates: Optional[list[ExchangeRate]] = None):
        self._rates: list[ExchangeRate] = rates or []

    @property
    def rates(self) -> list[ExchangeRate]:
        return self._rates

    def add_rate(self, rate: ExchangeRate) -> None:
        """Add an exchange rate."""
        self._rates.append(rate)

    def remove_rate(self, index: int) -> None:
        """Remove an exchange rate by index."""
        if 0 <= index < len(self._rates):
            self._rates.pop(index)
        else:
            raise IndexError(f"Rate index {index} out of range")

    def get_rate(self, from_currency: str, to_currency: str,
                 as_of: Optional[datetime] = None) -> ExchangeRate:
        """Get the most recent exchange rate between two currencies.

        If as_of is provided, returns the most recent rate at or before that time.
        """
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        if from_currency == to_currency:
            return ExchangeRate(
                from_currency=from_currency,
                to_currency=to_currency,
                rate=1.0,
                source="identity",
            )

        candidates = [
            r for r in self._rates
            if r.from_currency == from_currency and r.to_currency == to_currency
        ]

        if as_of is not None:
            candidates = [r for r in candidates if r.timestamp <= as_of]

        if not candidates:
            # Try inverse
            inverse = [
                r for r in self._rates
                if r.from_currency == to_currency and r.to_currency == from_currency
            ]
            if as_of is not None:
                inverse = [r for r in inverse if r.timestamp <= as_of]
            if inverse:
                best = max(inverse, key=lambda r: r.timestamp)
                return ExchangeRate(
                    from_currency=from_currency,
                    to_currency=to_currency,
                    rate=round(1.0 / best.rate, 6),
                    source=f"inverse of {best.source}",
                    timestamp=best.timestamp,
                )
            raise ExchangeRateNotFoundError(
                f"No exchange rate found for {from_currency} -> {to_currency}"
            )

        return max(candidates, key=lambda r: r.timestamp)

    def convert(self, amount: float, from_currency: str, to_currency: str,
                as_of: Optional[datetime] = None) -> float:
        """Convert an amount from one currency to another."""
        rate = self.get_rate(from_currency, to_currency, as_of)
        return round(amount * rate.rate, 2)

    def list_rates(self) -> list[ExchangeRate]:
        """List all exchange rates."""
        return list(self._rates)
