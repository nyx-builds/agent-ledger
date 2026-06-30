"""Depreciation engine for agent-ledger.

Automated fixed-asset depreciation with multiple methods:

* **Straight-line** — equal expense each period.
* **Declining balance** — fixed percentage of remaining book value.
* **Double-declining balance** — 2× straight-line rate on declining balance.
* **Units of production** — depreciation based on actual usage.

The engine creates depreciation schedules, posts monthly journal entries
to the ledger, and tracks accumulated depreciation per asset.

Depreciation entries follow standard double-entry conventions::

    Dr  Depreciation Expense      XXX
        Cr  Accumulated Depreciation   XXX

Fixed assets are tracked in ``LedgerData.metadata["fixed_assets"]`` so they
survive save/load round-trips with both JSON and SQLite backends.

Example::

    from agent_ledger.depreciation import DepreciationManager, DepreciationMethod

    dm = DepreciationManager(ledger)
    dm.create_asset(
        name="Server Rack",
        asset_account="equipment",
        accum_dep_account="accum_dep",
        dep_expense_account="dep_expense",
        cost=12000,
        salvage_value=2000,
        useful_life_months=60,
        method=DepreciationMethod.STRAIGHT_LINE,
    )
    # Post one month of depreciation
    dm.post_depreciation(asset_id)
    # Post all due depreciation as of today
    dm.post_all_depreciation()
"""

from __future__ import annotations

import calendar
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from .ledger import Ledger
from .exceptions import LedgerError


class DepreciationError(LedgerError):
    """Error related to depreciation."""


class AssetNotFoundError(DepreciationError):
    """Fixed asset not found."""


class DepreciationMethod(str, Enum):
    """Supported depreciation methods."""
    STRAIGHT_LINE = "straight_line"
    DECLINING_BALANCE = "declining_balance"
    DOUBLE_DECLINING = "double_declining"
    UNITS_OF_PRODUCTION = "units_of_production"


# ── Data structures ──────────────────────────────────────────────


@dataclass
class DepreciationEntry:
    """A single depreciation posting record.

    Attributes:
        date: Posting date.
        amount: Depreciation amount.
        period: Period label (e.g. "2024-03").
        method: Method used.
        units: Units consumed (for units-of-production only).
        entry_id: ID of the journal entry created (or None).
    """
    date: datetime
    amount: float
    period: str
    method: DepreciationMethod
    units: Optional[float] = None
    entry_id: Optional[str] = None


@dataclass
class FixedAsset:
    """A depreciable fixed asset.

    Attributes:
        id: Unique identifier.
        name: Human-readable name.
        description: Description.
        asset_account: Account code for the asset (debit balance).
        accum_dep_account: Account code for accumulated depreciation (credit balance).
        dep_expense_account: Account code for depreciation expense (debit balance).
        cost: Original acquisition cost.
        salvage_value: Estimated salvage/residual value.
        useful_life_months: Useful life in months (for time-based methods).
        method: Depreciation method.
        declining_rate: Custom rate for declining balance (default: auto from life).
        total_units: Total estimated production units (for units-of-production).
        units_consumed: Units consumed so far (for units-of-production).
        in_service_date: Date the asset was placed in service.
        disposal_date: Date the asset was disposed (None = active).
        depreciation_history: List of DepreciationEntry records.
        active: Whether the asset is active (not fully depreciated or disposed).
        metadata: Extra metadata.
        created_at: When the asset record was created.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    asset_account: str = ""
    accum_dep_account: str = ""
    dep_expense_account: str = ""
    cost: float = 0.0
    salvage_value: float = 0.0
    useful_life_months: int = 0
    method: DepreciationMethod = DepreciationMethod.STRAIGHT_LINE
    declining_rate: Optional[float] = None
    total_units: Optional[float] = None
    units_consumed: float = 0.0
    in_service_date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    disposal_date: Optional[datetime] = None
    depreciation_history: list[DepreciationEntry] = field(default_factory=list)
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ── Computed properties ─────────────────────────────────────

    @property
    def accumulated_depreciation(self) -> float:
        """Total depreciation posted to date."""
        return round(sum(d.amount for d in self.depreciation_history), 2)

    @property
    def book_value(self) -> float:
        """Current net book value (cost − accumulated depreciation)."""
        return round(self.cost - self.accumulated_depreciation, 2)

    @property
    def depreciable_base(self) -> float:
        """Amount that can be depreciated (cost − salvage value)."""
        return round(max(0, self.cost - self.salvage_value), 2)

    @property
    def months_depreciated(self) -> int:
        """Number of months depreciation has been posted."""
        return len(self.depreciation_history)

    @property
    def remaining_life_months(self) -> int:
        """Remaining months of useful life."""
        return max(0, self.useful_life_months - self.months_depreciated)

    @property
    def is_fully_depreciated(self) -> bool:
        """True if accumulated depreciation >= depreciable base."""
        return self.accumulated_depreciation >= self.depreciable_base - 0.01

    @property
    def straight_line_monthly(self) -> float:
        """Monthly depreciation under straight-line method."""
        if self.useful_life_months <= 0:
            return 0.0
        return round(self.depreciable_base / self.useful_life_months, 2)

    @property
    def effective_rate(self) -> float:
        """The declining-balance rate to use (custom or auto-computed)."""
        if self.declining_rate is not None:
            return self.declining_rate
        if self.method == DepreciationMethod.DOUBLE_DECLINING:
            if self.useful_life_months <= 0:
                return 0.0
            return 2.0 / self.useful_life_months
        if self.method == DepreciationMethod.DECLINING_BALANCE:
            if self.useful_life_months <= 0:
                return 0.0
            return 1.0 / self.useful_life_months
        return 0.0


# ── Manager ──────────────────────────────────────────────────────


class DepreciationManager:
    """Create, depreciate, and dispose of fixed assets.

    Assets are stored in ``ledger.data.metadata["fixed_assets"]``.
    """

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self._assets: list[FixedAsset] = []
        self._load()

    # ── Persistence ─────────────────────────────────────────────

    def _load(self) -> None:
        """Load assets from ledger metadata."""
        raw = self.ledger.data.metadata.get("fixed_assets", [])
        for rd in raw:
            history = [
                DepreciationEntry(
                    date=_parse_dt(h.get("date")) or datetime.now(timezone.utc),
                    amount=h.get("amount", 0.0),
                    period=h.get("period", ""),
                    method=DepreciationMethod(h.get("method", "straight_line")),
                    units=h.get("units"),
                    entry_id=h.get("entry_id"),
                )
                for h in rd.get("depreciation_history", [])
            ]
            asset = FixedAsset(
                id=rd.get("id", str(uuid.uuid4())),
                name=rd.get("name", ""),
                description=rd.get("description", ""),
                asset_account=rd.get("asset_account", ""),
                accum_dep_account=rd.get("accum_dep_account", ""),
                dep_expense_account=rd.get("dep_expense_account", ""),
                cost=rd.get("cost", 0.0),
                salvage_value=rd.get("salvage_value", 0.0),
                useful_life_months=rd.get("useful_life_months", 0),
                method=DepreciationMethod(rd.get("method", "straight_line")),
                declining_rate=rd.get("declining_rate"),
                total_units=rd.get("total_units"),
                units_consumed=rd.get("units_consumed", 0.0),
                in_service_date=_parse_dt(rd.get("in_service_date")) or datetime.now(timezone.utc),
                disposal_date=_parse_dt(rd.get("disposal_date")),
                depreciation_history=history,
                active=rd.get("active", True),
                metadata=rd.get("metadata", {}),
                created_at=_parse_dt(rd.get("created_at")) or datetime.now(timezone.utc),
            )
            self._assets.append(asset)

    def _save(self) -> None:
        """Persist assets to ledger metadata."""
        data = []
        for a in self._assets:
            data.append({
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "asset_account": a.asset_account,
                "accum_dep_account": a.accum_dep_account,
                "dep_expense_account": a.dep_expense_account,
                "cost": a.cost,
                "salvage_value": a.salvage_value,
                "useful_life_months": a.useful_life_months,
                "method": a.method.value,
                "declining_rate": a.declining_rate,
                "total_units": a.total_units,
                "units_consumed": a.units_consumed,
                "in_service_date": a.in_service_date.isoformat() if a.in_service_date else None,
                "disposal_date": a.disposal_date.isoformat() if a.disposal_date else None,
                "depreciation_history": [
                    {
                        "date": h.date.isoformat() if h.date else None,
                        "amount": h.amount,
                        "period": h.period,
                        "method": h.method.value,
                        "units": h.units,
                        "entry_id": h.entry_id,
                    }
                    for h in a.depreciation_history
                ],
                "active": a.active,
                "metadata": a.metadata,
                "created_at": a.created_at.isoformat(),
            })
        self.ledger.data.metadata["fixed_assets"] = data
        self.ledger.save()

    # ── CRUD ─────────────────────────────────────────────────────

    def create_asset(
        self,
        name: str,
        asset_account: str,
        accum_dep_account: str,
        dep_expense_account: str,
        cost: float,
        salvage_value: float = 0.0,
        useful_life_months: int = 0,
        method: str | DepreciationMethod = DepreciationMethod.STRAIGHT_LINE,
        declining_rate: Optional[float] = None,
        total_units: Optional[float] = None,
        in_service_date: Optional[datetime] = None,
        description: str = "",
    ) -> FixedAsset:
        """Register a new fixed asset for depreciation tracking.

        Args:
            name: Asset name.
            asset_account: Account code holding the asset's cost.
            accum_dep_account: Account code for accumulated depreciation (contra-asset).
            dep_expense_account: Account code for depreciation expense.
            cost: Acquisition cost.
            salvage_value: Estimated salvage value at end of life.
            useful_life_months: Useful life in months (required for time-based methods).
            method: Depreciation method.
            declining_rate: Custom rate for declining balance (overrides auto).
            total_units: Total estimated production units (for units-of-production).
            in_service_date: Date placed in service (default: now).
            description: Optional description.

        Returns:
            The created FixedAsset.
        """
        if isinstance(method, str):
            method = DepreciationMethod(method)

        if cost <= 0:
            raise DepreciationError("Cost must be positive")

        if salvage_value < 0:
            raise DepreciationError("Salvage value cannot be negative")

        if salvage_value >= cost:
            raise DepreciationError("Salvage value cannot exceed cost")

        # Validate accounts exist
        for acct in [asset_account, accum_dep_account, dep_expense_account]:
            self.ledger.get_account(acct.strip().lower())

        if method != DepreciationMethod.UNITS_OF_PRODUCTION and useful_life_months <= 0:
            raise DepreciationError("Useful life (months) must be positive for time-based methods")

        if method == DepreciationMethod.UNITS_OF_PRODUCTION and not total_units:
            raise DepreciationError("total_units is required for units-of-production method")

        asset = FixedAsset(
            name=name,
            description=description,
            asset_account=asset_account.strip().lower(),
            accum_dep_account=accum_dep_account.strip().lower(),
            dep_expense_account=dep_expense_account.strip().lower(),
            cost=round(cost, 2),
            salvage_value=round(salvage_value, 2),
            useful_life_months=useful_life_months,
            method=method,
            declining_rate=declining_rate,
            total_units=total_units,
            in_service_date=_ensure_aware(in_service_date) if in_service_date else datetime.now(timezone.utc),
            active=True,
        )
        self._assets.append(asset)
        self._save()
        return asset

    def get(self, asset_id: str) -> FixedAsset:
        for a in self._assets:
            if a.id == asset_id:
                return a
        raise AssetNotFoundError(f"Fixed asset '{asset_id}' not found")

    def list_assets(self, active_only: bool = False) -> list[FixedAsset]:
        """List all fixed assets."""
        assets = list(self._assets)
        if active_only:
            assets = [a for a in assets if a.active]
        return sorted(assets, key=lambda a: a.created_at)

    def update(
        self,
        asset_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        salvage_value: Optional[float] = None,
        useful_life_months: Optional[int] = None,
        active: Optional[bool] = None,
    ) -> FixedAsset:
        """Update asset metadata. Does not change depreciation method or accounts."""
        asset = self.get(asset_id)
        if name is not None:
            asset.name = name
        if description is not None:
            asset.description = description
        if salvage_value is not None:
            if salvage_value < 0:
                raise DepreciationError("Salvage value cannot be negative")
            if salvage_value >= asset.cost:
                raise DepreciationError("Salvage value cannot exceed cost")
            asset.salvage_value = round(salvage_value, 2)
        if useful_life_months is not None:
            asset.useful_life_months = useful_life_months
        if active is not None:
            asset.active = active
        self._save()
        return asset

    def delete(self, asset_id: str) -> None:
        """Delete an asset record. Does NOT reverse posted depreciation entries."""
        asset = self.get(asset_id)
        self._assets.remove(asset)
        self._save()

    # ── Depreciation computation ────────────────────────────────

    def compute_period_depreciation(
        self,
        asset: FixedAsset,
        as_of: Optional[datetime] = None,
    ) -> float:
        """Compute the depreciation amount for a single period.

        For time-based methods, this is the monthly amount.
        For units-of-production, returns 0 (use ``post_units``).

        For declining balance, the rate is applied to the current book value
        (not the depreciable base).  The method automatically switches to
        straight-line when that produces a higher expense, ensuring the asset
        reaches salvage value by end of life.
        """
        if not asset.active or asset.is_fully_depreciated:
            return 0.0

        if asset.method == DepreciationMethod.STRAIGHT_LINE:
            return asset.straight_line_monthly

        if asset.method in (DepreciationMethod.DECLINING_BALANCE,
                            DepreciationMethod.DOUBLE_DECLINING):
            rate = asset.effective_rate
            current_book = asset.book_value

            # Declining balance: rate × current book value
            declining_amount = round(current_book * rate, 2)

            # Check if straight-line for the remaining life would be higher
            remaining_life = asset.remaining_life_months
            if remaining_life > 0:
                remaining_depreciable = current_book - asset.salvage_value
                sl_amount = round(remaining_depreciable / remaining_life, 2)

                # Use the higher of the two (optimal switch point)
                return max(declining_amount, sl_amount)
            return declining_amount

        return 0.0  # Units of production handled separately

    def post_depreciation(
        self,
        asset_id: str,
        as_of: Optional[datetime] = None,
    ) -> Optional[DepreciationEntry]:
        """Post one period of depreciation for an asset.

        Creates a journal entry:
            Dr  Depreciation Expense
                Cr  Accumulated Depreciation

        Args:
            asset_id: The asset to depreciate.
            as_of: Posting date (default: now).

        Returns:
            The DepreciationEntry record, or None if no depreciation posted
            (asset inactive or fully depreciated).
        """
        asset = self.get(asset_id)
        as_of = _ensure_aware(as_of) if as_of else datetime.now(timezone.utc)

        if not asset.active or asset.is_fully_depreciated:
            return None

        amount = self.compute_period_depreciation(asset, as_of)

        # Don't depreciate below salvage value
        remaining = asset.book_value - asset.salvage_value
        if amount > remaining:
            amount = round(remaining, 2)

        if amount <= 0:
            return None

        period = as_of.strftime("%Y-%m")

        # Post the journal entry
        entry = self.ledger.post_entry(
            description=f"Depreciation: {asset.name} ({period})",
            lines=[
                # Debit depreciation expense
                (asset.dep_expense_account, amount, 0),
                # Credit accumulated depreciation
                (asset.accum_dep_account, 0, amount),
            ],
            tags=["depreciation", f"asset:{asset.id}"],
            metadata={
                "depreciation": True,
                "asset_id": asset.id,
                "asset_name": asset.name,
                "period": period,
                "method": asset.method.value,
            },
            timestamp=as_of,
        )

        dep_entry = DepreciationEntry(
            date=as_of,
            amount=amount,
            period=period,
            method=asset.method,
            entry_id=entry.id,
        )
        asset.depreciation_history.append(dep_entry)

        # Check if fully depreciated
        if asset.is_fully_depreciated:
            asset.active = False

        self._save()
        return dep_entry

    def post_units(
        self,
        asset_id: str,
        units: float,
        as_of: Optional[datetime] = None,
    ) -> Optional[DepreciationEntry]:
        """Post depreciation based on units consumed (units-of-production method).

        Args:
            asset_id: The asset to depreciate.
            units: Number of units consumed this period.
            as_of: Posting date.

        Returns:
            The DepreciationEntry record, or None if no depreciation posted.
        """
        asset = self.get(asset_id)
        as_of = _ensure_aware(as_of) if as_of else datetime.now(timezone.utc)

        if not asset.active or asset.is_fully_depreciated:
            return None

        if asset.method != DepreciationMethod.UNITS_OF_PRODUCTION:
            raise DepreciationError(
                "post_units requires units-of-production method"
            )

        if units <= 0:
            raise DepreciationError("Units must be positive")

        if not asset.total_units or asset.total_units <= 0:
            raise DepreciationError("Asset has no total_units configured")

        rate = asset.depreciable_base / asset.total_units
        amount = round(units * rate, 2)

        # Don't depreciate below salvage
        remaining = asset.book_value - asset.salvage_value
        if amount > remaining:
            amount = round(remaining, 2)
            units = round(remaining / rate, 2)  # adjust units to actual

        if amount <= 0:
            return None

        period = as_of.strftime("%Y-%m")

        entry = self.ledger.post_entry(
            description=f"Depreciation (units): {asset.name} ({period})",
            lines=[
                (asset.dep_expense_account, amount, 0),
                (asset.accum_dep_account, 0, amount),
            ],
            tags=["depreciation", f"asset:{asset.id}"],
            metadata={
                "depreciation": True,
                "asset_id": asset.id,
                "asset_name": asset.name,
                "period": period,
                "method": "units_of_production",
                "units": units,
            },
            timestamp=as_of,
        )

        dep_entry = DepreciationEntry(
            date=as_of,
            amount=amount,
            period=period,
            method=asset.method,
            units=units,
            entry_id=entry.id,
        )
        asset.depreciation_history.append(dep_entry)
        asset.units_consumed = round(asset.units_consumed + units, 2)

        if asset.is_fully_depreciated:
            asset.active = False

        self._save()
        return dep_entry

    def post_all_depreciation(self, as_of: Optional[datetime] = None) -> list[dict]:
        """Post depreciation for all active, non-fully-depreciated assets.

        Args:
            as_of: Posting date (default: now).

        Returns:
            List of dicts with asset_id, asset_name, amount, and status.
        """
        as_of = _ensure_aware(as_of) if as_of else datetime.now(timezone.utc)
        results = []

        for asset in list(self._assets):
            if not asset.active or asset.is_fully_depreciated:
                results.append({
                    "asset_id": asset.id,
                    "asset_name": asset.name,
                    "amount": 0,
                    "status": "skipped",
                })
                continue

            try:
                if asset.method == DepreciationMethod.UNITS_OF_PRODUCTION:
                    # Skip — requires manual unit input
                    results.append({
                        "asset_id": asset.id,
                        "asset_name": asset.name,
                        "amount": 0,
                        "status": "skipped_units_required",
                    })
                    continue

                dep = self.post_depreciation(asset.id, as_of=as_of)
                results.append({
                    "asset_id": asset.id,
                    "asset_name": asset.name,
                    "amount": dep.amount if dep else 0,
                    "status": "posted" if dep else "skipped",
                })
            except Exception as e:
                results.append({
                    "asset_id": asset.id,
                    "asset_name": asset.name,
                    "amount": 0,
                    "status": "error",
                    "error": str(e),
                })

        return results

    def dispose(
        self,
        asset_id: str,
        disposal_value: float = 0.0,
        disposal_account: Optional[str] = None,
        as_of: Optional[datetime] = None,
    ) -> dict:
        """Dispose of a fixed asset.

        Posts the disposal entry:
        1. Remove accumulated depreciation (debit accum. dep.)
        2. Remove asset at cost (credit asset account)
        3. Record cash/proceeds (debit disposal account if provided)
        4. Record gain or loss on disposal

        Args:
            asset_id: The asset to dispose.
            disposal_value: Amount received from disposal.
            disposal_account: Account to debit for proceeds (e.g. "cash").
            as_of: Disposal date.

        Returns:
            Dict with entry_id, gain_or_loss, and details.
        """
        asset = self.get(asset_id)
        as_of = _ensure_aware(as_of) if as_of else datetime.now(timezone.utc)

        if asset.disposal_date is not None:
            raise DepreciationError(f"Asset '{asset.name}' already disposed on {asset.disposal_date}")

        accum_dep = asset.accumulated_depreciation
        book_value = asset.book_value

        # If the asset is fully depreciated and no disposal value,
        # we still mark it disposed without a zero-value entry that would
        # violate the journal entry rules.
        if accum_dep == 0 and asset.cost == 0:
            raise DepreciationError("Cannot dispose an asset with zero cost")

        # Build disposal journal entry — skip zero-amount lines
        lines = []

        # Debit accumulated depreciation (remove it)
        if accum_dep > 0:
            lines.append((asset.accum_dep_account, accum_dep, 0))

        # Credit asset at cost (remove it)
        lines.append((asset.asset_account, 0, asset.cost))

        # If disposal value > 0, debit the disposal account
        if disposal_value > 0 and disposal_account:
            self.ledger.get_account(disposal_account.strip().lower())
            lines.append((disposal_account.strip().lower(), disposal_value, 0))

        # Gain or loss on disposal
        gain_or_loss = round(disposal_value - book_value, 2)

        # We need a gain/loss account.  We'll use the dep_expense_account
        # as a fallback if no specific gain/loss account is available.
        # Gain → credit expense account (reduces expense)
        # Loss → debit expense account (increases expense)
        if abs(gain_or_loss) > 0.01:
            if gain_or_loss > 0:
                # Gain: credit the expense account
                lines.append((asset.dep_expense_account, 0, abs(gain_or_loss)))
            else:
                # Loss: debit the expense account
                lines.append((asset.dep_expense_account, abs(gain_or_loss), 0))

        # Need at least 2 lines for a valid journal entry
        if len(lines) < 2:
            # Asset cost is the only line — add the expense account as a
            # zero-effect placeholder (shouldn't normally happen)
            lines.append((asset.dep_expense_account, 0, 0))

        entry = self.ledger.post_entry(
            description=f"Disposal: {asset.name}",
            lines=lines,
            tags=["disposal", f"asset:{asset.id}"],
            metadata={
                "disposal": True,
                "asset_id": asset.id,
                "asset_name": asset.name,
                "cost": asset.cost,
                "accumulated_depreciation": accum_dep,
                "book_value": book_value,
                "disposal_value": disposal_value,
                "gain_or_loss": gain_or_loss,
            },
            timestamp=as_of,
        )

        asset.disposal_date = as_of
        asset.active = False
        self._save()

        return {
            "entry_id": entry.id,
            "asset_name": asset.name,
            "cost": asset.cost,
            "accumulated_depreciation": accum_dep,
            "book_value": book_value,
            "disposal_value": disposal_value,
            "gain_or_loss": gain_or_loss,
            "gain": gain_or_loss > 0,
        }

    def get_schedule(
        self,
        asset_id: str,
        periods: Optional[int] = None,
    ) -> list[dict]:
        """Generate a projected depreciation schedule for an asset.

        Args:
            asset_id: The asset to project.
            periods: Number of periods to project (default: to end of life).

        Returns:
            List of dicts with period, depreciation, accumulated, and book_value.
        """
        asset = self.get(asset_id)
        schedule = []

        accum = asset.accumulated_depreciation
        book = asset.book_value

        if periods is None:
            if asset.method == DepreciationMethod.UNITS_OF_PRODUCTION:
                periods = 12  # Can't project without unit estimates
            else:
                periods = asset.remaining_life_months

        for i in range(periods):
            if asset.method == DepreciationMethod.UNITS_OF_PRODUCTION:
                # Can't project — skip
                break

            remaining = book - asset.salvage_value
            if remaining <= 0.01:
                break

            if asset.method == DepreciationMethod.STRAIGHT_LINE:
                dep = asset.straight_line_monthly
                # On the last period, take whatever remains to hit salvage exactly
                remaining_life = asset.useful_life_months - asset.months_depreciated - i
                if remaining_life <= 1:
                    dep = round(remaining, 2)
            else:
                rate = asset.effective_rate
                declining = round(book * rate, 2)
                remaining_life = asset.useful_life_months - asset.months_depreciated - i
                if remaining_life > 0:
                    sl = round(remaining / remaining_life, 2)
                    dep = max(declining, sl)
                else:
                    dep = declining

            dep = min(dep, remaining)
            accum = round(accum + dep, 2)
            book = round(book - dep, 2)

            period_date = asset.in_service_date.replace(
                day=1,
            )
            # Add months: depreciation history + i + 1
            total_months_offset = asset.months_depreciated + i + 1
            year = period_date.year + (period_date.month - 1 + total_months_offset) // 12
            month = (period_date.month - 1 + total_months_offset) % 12 + 1
            period_label = f"{year}-{month:02d}"

            schedule.append({
                "period": period_label,
                "depreciation": round(dep, 2),
                "accumulated_depreciation": accum,
                "book_value": max(book, asset.salvage_value),
            })

        return schedule


# ── Helpers ──────────────────────────────────────────────────────


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return _ensure_aware(dt)
    except (ValueError, AttributeError):
        return None


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def format_asset_list(assets: list[FixedAsset]) -> str:
    """Format a list of assets as text."""
    if not assets:
        return "No fixed assets found."

    lines = []
    lines.append(
        f"  {'Name':<20} {'Method':<15} {'Cost':>12} {'Accum Dep':>12} "
        f"{'Book Value':>12} {'Active':>7}"
    )
    lines.append("-" * 85)
    for a in assets:
        method_short = a.method.value.replace("_", " ").title()
        lines.append(
            f"  {a.name[:20]:<20} {method_short:<15} {a.cost:>12,.2f} "
            f"{a.accumulated_depreciation:>12,.2f} {a.book_value:>12,.2f} "
            f"{'Yes' if a.active else 'No':>7}"
        )
    return "\n".join(lines)


def format_asset_detail(asset: FixedAsset) -> str:
    """Format a single asset as a detailed text report."""
    lines = []
    lines.append(f"FIXED ASSET: {asset.name}")
    lines.append(f"ID: {asset.id}")
    if asset.description:
        lines.append(f"Description: {asset.description}")
    lines.append(f"Method: {asset.method.value.replace('_', ' ').title()}")
    lines.append(f"Cost: {asset.cost:,.2f}")
    lines.append(f"Salvage Value: {asset.salvage_value:,.2f}")
    lines.append(f"Useful Life: {asset.useful_life_months} months")
    lines.append(f"In Service: {asset.in_service_date.strftime('%Y-%m-%d')}")
    lines.append(f"Depreciable Base: {asset.depreciable_base:,.2f}")
    lines.append(f"Accumulated Depreciation: {asset.accumulated_depreciation:,.2f}")
    lines.append(f"Book Value: {asset.book_value:,.2f}")
    lines.append(f"Months Depreciated: {asset.months_depreciated}")
    lines.append(f"Remaining Life: {asset.remaining_life_months} months")
    lines.append(f"Active: {'Yes' if asset.active else 'No'}")
    if asset.disposal_date:
        lines.append(f"Disposal Date: {asset.disposal_date.strftime('%Y-%m-%d')}")

    if asset.method == DepreciationMethod.UNITS_OF_PRODUCTION:
        total_units_str = f"{asset.total_units:,.0f}" if asset.total_units else "0"
        lines.append(f"Total Units: {total_units_str}")
        lines.append(f"Units Consumed: {asset.units_consumed:,.0f}")

    if asset.depreciation_history:
        lines.append("")
        lines.append("Depreciation History:")
        lines.append(f"  {'Period':<10} {'Amount':>12} {'Method':<20}")
        lines.append(f"  {'-' * 45}")
        for h in asset.depreciation_history:
            method_short = h.method.value.replace("_", " ").title()
            lines.append(f"  {h.period:<10} {h.amount:>12,.2f} {method_short:<20}")

    return "\n".join(lines)


def format_depreciation_schedule(schedule: list[dict], asset_name: str = "") -> str:
    """Format a depreciation schedule as text."""
    lines = []
    title = "DEPRECIATION SCHEDULE"
    if asset_name:
        title += f": {asset_name}"
    lines.append(title)
    lines.append(f"  {'Period':<10} {'Depreciation':>14} {'Accum Dep':>14} {'Book Value':>14}")
    lines.append(f"  {'-' * 56}")
    for row in schedule:
        lines.append(
            f"  {row['period']:<10} {row['depreciation']:>14,.2f} "
            f"{row['accumulated_depreciation']:>14,.2f} {row['book_value']:>14,.2f}"
        )
    return "\n".join(lines)
