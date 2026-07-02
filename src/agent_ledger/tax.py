"""Tax reporting for agent-ledger — generate tax summaries and reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .models import AccountType, AccountBalance
from .ledger import Ledger
from .reports import _ensure_aware


@dataclass
class TaxCategory:
    """A tax category mapping account types/codes to tax treatment."""
    name: str
    tax_code: str
    account_codes: list[str] = field(default_factory=list)
    account_types: list[AccountType] = field(default_factory=list)
    deductible: bool = True
    rate: float = 0.0  # Optional tax rate for withholding/estimation


@dataclass
class TaxLineItem:
    """A single line in a tax summary."""
    account_code: str
    account_name: str
    account_type: AccountType
    amount: float
    tax_category: str
    tax_code: str
    deductible: bool


@dataclass
class TaxSummary:
    """Tax summary report."""
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    items: list[TaxLineItem] = field(default_factory=list)
    total_revenue: float = 0.0
    total_deductible_expenses: float = 0.0
    total_nondeductible_expenses: float = 0.0
    taxable_income: float = 0.0
    estimated_tax: float = 0.0
    tax_rate_used: float = 0.0


# Default tax categories for US-style tax reporting
DEFAULT_TAX_CATEGORIES: list[TaxCategory] = [
    TaxCategory(
        name="Business Income",
        tax_code="INC",
        account_types=[AccountType.REVENUE],
        deductible=True,
    ),
    TaxCategory(
        name="Cost of Goods Sold",
        tax_code="COGS",
        account_types=[AccountType.EXPENSE],
        deductible=True,
    ),
    TaxCategory(
        name="Operating Expenses",
        tax_code="OPEX",
        account_types=[AccountType.EXPENSE],
        deductible=True,
    ),
    TaxCategory(
        name="Non-Deductible Expenses",
        tax_code="NONDED",
        deductible=False,
        rate=0.0,
    ),
]


def _classify_account(
    account_code: str,
    account_type: AccountType,
    categories: list[TaxCategory],
) -> TaxCategory:
    """Classify an account into a tax category.

    First checks explicit account_codes, then falls back to account_types.
    Returns the first matching category, or a default.
    """
    for cat in categories:
        if account_code in cat.account_codes:
            return cat

    for cat in categories:
        if account_type in cat.account_types:
            return cat

    # Default: deductible if expense or revenue
    if account_type in (AccountType.REVENUE, AccountType.EXPENSE):
        return TaxCategory(
            name="Other" if account_type == AccountType.EXPENSE else "Other Income",
            tax_code="OTHER",
            account_types=[account_type],
            deductible=True,
        )

    return TaxCategory(
        name="Balance Sheet",
        tax_code="BS",
        deductible=False,
    )


def generate_tax_summary(
    ledger: Ledger,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    tax_rate: float = 0.0,
    categories: Optional[list[TaxCategory]] = None,
) -> TaxSummary:
    """Generate a tax summary report.

    Classifies all accounts into tax categories and computes
    taxable income and estimated tax.

    Args:
        ledger: The ledger to generate the report from
        from_date: Optional start date for the period
        to_date: Optional end date for the period
        tax_rate: Tax rate for estimated tax calculation (e.g., 0.21 for 21%)
        categories: Custom tax categories (uses defaults if None)

    Returns:
        TaxSummary with classified items and totals
    """
    cats = categories or DEFAULT_TAX_CATEGORIES

    # Get balances for the period
    if from_date is not None or to_date is not None:
        from .reports import _compute_balances_from_entries
        filtered_entries = list(ledger.data.entries)
        if from_date is not None:
            from_aware = _ensure_aware(from_date)
            filtered_entries = [e for e in filtered_entries if _ensure_aware(e.timestamp) >= from_aware]
        if to_date is not None:
            to_aware = _ensure_aware(to_date)
            filtered_entries = [e for e in filtered_entries if _ensure_aware(e.timestamp) <= to_aware]
        balances = _compute_balances_from_entries(ledger, filtered_entries)
    else:
        balances = ledger.get_all_balances()

    items = []
    total_revenue = 0.0
    total_deductible = 0.0
    total_nondeductible = 0.0

    for b in sorted(balances, key=lambda x: x.account_code):
        if b.balance == 0:
            continue

        cat = _classify_account(b.account_code, b.account_type, cats)

        items.append(TaxLineItem(
            account_code=b.account_code,
            account_name=b.account_name,
            account_type=b.account_type,
            amount=b.balance,
            tax_category=cat.name,
            tax_code=cat.tax_code,
            deductible=cat.deductible,
        ))

        if b.account_type == AccountType.REVENUE:
            total_revenue += b.balance
        elif b.account_type == AccountType.EXPENSE:
            if cat.deductible:
                total_deductible += b.balance
            else:
                total_nondeductible += b.balance

    taxable_income = round(total_revenue - total_deductible, 2)
    estimated_tax = round(taxable_income * tax_rate, 2) if tax_rate > 0 else 0.0

    return TaxSummary(
        period_start=from_date,
        period_end=to_date,
        items=items,
        total_revenue=round(total_revenue, 2),
        total_deductible_expenses=round(total_deductible, 2),
        total_nondeductible_expenses=round(total_nondeductible, 2),
        taxable_income=taxable_income,
        estimated_tax=estimated_tax,
        tax_rate_used=tax_rate,
    )


def format_tax_summary(report: TaxSummary) -> str:
    """Format a tax summary as text."""
    lines = []
    lines.append("TAX SUMMARY")
    if report.period_start and report.period_end:
        lines.append(
            f"Period: {report.period_start.strftime('%Y-%m-%d')} to "
            f"{report.period_end.strftime('%Y-%m-%d')}"
        )
    lines.append("")

    lines.append(f"{'Code':<12} {'Account':<25} {'Category':<20} {'Amount':>12} {'Deduct':>8}")
    lines.append("-" * 79)

    for item in report.items:
        deduct_str = "Yes" if item.deductible else "No"
        lines.append(
            f"{item.account_code:<12} {item.account_name:<25} "
            f"{item.tax_category:<20} {item.amount:>12,.2f} {deduct_str:>8}"
        )

    lines.append("")
    lines.append(f"{'Total Revenue':<57} {report.total_revenue:>12,.2f}")
    lines.append(f"{'Total Deductible Expenses':<57} {report.total_deductible_expenses:>12,.2f}")
    lines.append(f"{'Total Non-Deductible Expenses':<57} {report.total_nondeductible_expenses:>12,.2f}")
    lines.append(f"{'Taxable Income':<57} {report.taxable_income:>12,.2f}")

    if report.tax_rate_used > 0:
        lines.append("")
        lines.append(f"{'Tax Rate':<57} {report.tax_rate_used * 100:>11.1f}%")
        lines.append(f"{'Estimated Tax':<57} {report.estimated_tax:>12,.2f}")

    return "\n".join(lines)
