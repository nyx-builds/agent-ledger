"""Fiscal year management for agent-ledger — define fiscal years and periods."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import AccountType
from .ledger import Ledger
from .exceptions import LedgerError


class FiscalYearError(LedgerError):
    """Error related to fiscal year operations."""
    pass


class OverlappingFiscalYearError(FiscalYearError):
    """Fiscal years cannot overlap."""
    pass


class FiscalYearNotFoundError(FiscalYearError):
    """Fiscal year not found."""
    pass


class FiscalYearClosedError(FiscalYearError):
    """Operation not allowed on a closed fiscal year."""
    pass


@dataclass
class FiscalPeriod:
    """A period within a fiscal year (e.g., a month or quarter)."""
    name: str
    start_date: datetime
    end_date: datetime
    status: str = "open"  # open, closed
    period_type: str = "month"  # month, quarter, year


@dataclass
class FiscalYear:
    """A fiscal year definition."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    start_date: datetime = field(default_factory=lambda: datetime(2024, 1, 1, tzinfo=timezone.utc))
    end_date: datetime = field(default_factory=lambda: datetime(2024, 12, 31, tzinfo=timezone.utc))
    status: str = "open"  # open, closed
    periods: list[FiscalPeriod] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FiscalYearManager:
    """Manage fiscal years and periods for a ledger."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self._fiscal_years: list[FiscalYear] = []
        self._load()

    def _load(self) -> None:
        """Load fiscal years from ledger metadata."""
        fy_data = self.ledger.data.metadata.get("fiscal_years", []) if hasattr(self.ledger.data, 'metadata') else []
        for fd in fy_data:
            periods = []
            for pd in fd.get("periods", []):
                periods.append(FiscalPeriod(
                    name=pd.get("name", ""),
                    start_date=datetime.fromisoformat(pd["start_date"]) if pd.get("start_date") else datetime.now(timezone.utc),
                    end_date=datetime.fromisoformat(pd["end_date"]) if pd.get("end_date") else datetime.now(timezone.utc),
                    status=pd.get("status", "open"),
                    period_type=pd.get("period_type", "month"),
                ))
            self._fiscal_years.append(FiscalYear(
                id=fd.get("id", str(uuid.uuid4())),
                name=fd.get("name", ""),
                start_date=datetime.fromisoformat(fd["start_date"]) if fd.get("start_date") else datetime.now(timezone.utc),
                end_date=datetime.fromisoformat(fd["end_date"]) if fd.get("end_date") else datetime.now(timezone.utc),
                status=fd.get("status", "open"),
                periods=periods,
                created_at=datetime.fromisoformat(fd["created_at"]) if fd.get("created_at") else datetime.now(timezone.utc),
            ))

    def _save(self) -> None:
        """Save fiscal years to ledger metadata."""
        fy_data = []
        for fy in self._fiscal_years:
            periods_data = []
            for p in fy.periods:
                periods_data.append({
                    "name": p.name,
                    "start_date": p.start_date.isoformat(),
                    "end_date": p.end_date.isoformat(),
                    "status": p.status,
                    "period_type": p.period_type,
                })
            fy_data.append({
                "id": fy.id,
                "name": fy.name,
                "start_date": fy.start_date.isoformat(),
                "end_date": fy.end_date.isoformat(),
                "status": fy.status,
                "periods": periods_data,
                "created_at": fy.created_at.isoformat(),
            })

        # Store in ledger data
        if not hasattr(self.ledger.data, 'metadata') or self.ledger.data.metadata is None:
            # Add metadata field to LedgerData if it doesn't exist
            self.ledger.data.metadata = {}
        self.ledger.data.metadata["fiscal_years"] = fy_data
        self.ledger.save()

    def create_fiscal_year(
        self,
        name: str,
        start_date: datetime,
        end_date: datetime,
        auto_periods: bool = True,
        period_type: str = "month",
    ) -> FiscalYear:
        """Create a new fiscal year.

        Args:
            name: Display name (e.g., "FY 2024")
            start_date: First day of the fiscal year
            end_date: Last day of the fiscal year
            auto_periods: Automatically generate periods
            period_type: Type of periods to generate ("month" or "quarter")

        Returns:
            The created FiscalYear

        Raises:
            OverlappingFiscalYearError: If the new year overlaps with an existing one
        """
        # Check for overlap
        for fy in self._fiscal_years:
            if (start_date < fy.end_date and end_date > fy.start_date):
                raise OverlappingFiscalYearError(
                    f"Fiscal year overlaps with '{fy.name}' "
                    f"({fy.start_date.strftime('%Y-%m-%d')} to {fy.end_date.strftime('%Y-%m-%d')})"
                )

        fy = FiscalYear(
            name=name,
            start_date=start_date,
            end_date=end_date,
        )

        if auto_periods:
            fy.periods = self._generate_periods(fy, period_type)

        self._fiscal_years.append(fy)
        self._save()
        return fy

    def _generate_periods(
        self,
        fy: FiscalYear,
        period_type: str = "month",
    ) -> list[FiscalPeriod]:
        """Generate periods for a fiscal year.

        Args:
            fy: The fiscal year to generate periods for
            period_type: "month" or "quarter"

        Returns:
            List of FiscalPeriod objects
        """
        periods = []
        current = fy.start_date

        if period_type == "month":
            while current < fy.end_date:
                # Find end of month
                if current.month == 12:
                    next_month = current.replace(year=current.year + 1, month=1, day=1)
                else:
                    next_month = current.replace(month=current.month + 1, day=1)

                period_end = min(next_month - __import__("datetime").timedelta(days=1), fy.end_date)
                # Ensure end of day
                period_end = period_end.replace(hour=23, minute=59, second=59)

                periods.append(FiscalPeriod(
                    name=current.strftime("%B %Y"),
                    start_date=current,
                    end_date=period_end,
                    status="open",
                    period_type="month",
                ))
                current = next_month

        elif period_type == "quarter":
            quarter_num = 0
            while current < fy.end_date:
                quarter_num += 1
                # 3 months ahead
                quarter_month = current.month + 3
                quarter_year = current.year
                if quarter_month > 12:
                    quarter_month -= 12
                    quarter_year += 1
                quarter_end_start = current.replace(year=quarter_year, month=quarter_month, day=1)
                period_end = min(
                    quarter_end_start - __import__("datetime").timedelta(days=1),
                    fy.end_date,
                )
                period_end = period_end.replace(hour=23, minute=59, second=59)

                periods.append(FiscalPeriod(
                    name=f"Q{quarter_num} {current.year}",
                    start_date=current,
                    end_date=period_end,
                    status="open",
                    period_type="quarter",
                ))
                current = quarter_end_start

        return periods

    def get_fiscal_year(self, fy_id: str) -> FiscalYear:
        """Get a fiscal year by ID."""
        for fy in self._fiscal_years:
            if fy.id == fy_id:
                return fy
        raise FiscalYearNotFoundError(f"Fiscal year '{fy_id}' not found")

    def list_fiscal_years(self, status: Optional[str] = None) -> list[FiscalYear]:
        """List all fiscal years, optionally filtered by status."""
        years = list(self._fiscal_years)
        if status:
            years = [fy for fy in years if fy.status == status]
        return sorted(years, key=lambda fy: fy.start_date)

    def get_active_fiscal_year(self) -> Optional[FiscalYear]:
        """Get the currently active (open) fiscal year containing today's date."""
        now = datetime.now(timezone.utc)
        for fy in self._fiscal_years:
            if fy.status == "open" and fy.start_date <= now <= fy.end_date:
                return fy
        return None

    def close_fiscal_year(self, fy_id: str) -> FiscalYear:
        """Close a fiscal year and all its open periods.

        Args:
            fy_id: ID of the fiscal year to close

        Returns:
            The closed FiscalYear

        Raises:
            FiscalYearNotFoundError: If the fiscal year doesn't exist
            FiscalYearClosedError: If already closed
        """
        fy = self.get_fiscal_year(fy_id)

        if fy.status == "closed":
            raise FiscalYearClosedError(f"Fiscal year '{fy.name}' is already closed")

        fy.status = "closed"
        for period in fy.periods:
            period.status = "closed"

        self._save()
        return fy

    def reopen_fiscal_year(self, fy_id: str) -> FiscalYear:
        """Reopen a closed fiscal year.

        Args:
            fy_id: ID of the fiscal year to reopen

        Returns:
            The reopened FiscalYear
        """
        fy = self.get_fiscal_year(fy_id)

        if fy.status == "open":
            raise FiscalYearError(f"Fiscal year '{fy.name}' is already open")

        fy.status = "open"
        for period in fy.periods:
            period.status = "open"

        self._save()
        return fy

    def get_period_for_date(self, date: datetime) -> Optional[FiscalPeriod]:
        """Find which fiscal period a date falls into."""
        for fy in self._fiscal_years:
            for period in fy.periods:
                if period.start_date <= date <= period.end_date:
                    return period
        return None

    def close_period(self, fy_id: str, period_name: str) -> FiscalPeriod:
        """Close a specific period within a fiscal year.

        Args:
            fy_id: ID of the fiscal year
            period_name: Name of the period to close

        Returns:
            The closed FiscalPeriod
        """
        fy = self.get_fiscal_year(fy_id)

        if fy.status == "closed":
            raise FiscalYearClosedError(
                f"Cannot close period in closed fiscal year '{fy.name}'"
            )

        for period in fy.periods:
            if period.name == period_name:
                if period.status == "closed":
                    raise FiscalYearError(f"Period '{period_name}' is already closed")
                period.status = "closed"
                self._save()
                return period

        raise FiscalYearNotFoundError(f"Period '{period_name}' not found in fiscal year '{fy.name}'")

    def delete_fiscal_year(self, fy_id: str) -> None:
        """Delete a fiscal year (only if open).

        Args:
            fy_id: ID of the fiscal year to delete
        """
        fy = self.get_fiscal_year(fy_id)
        self._fiscal_years.remove(fy)
        self._save()
