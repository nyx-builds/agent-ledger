"""Financial ratios and KPIs for agent-ledger.

Compute standard financial ratios that an autonomous agent can use to make
data-driven decisions about its financial health.  All ratios are derived from
the ledger's double-entry data — no external sources required.

Ratios computed
---------------
* **Current ratio** — current assets / current liabilities
* **Quick ratio** (acid test) — (current assets − inventory) / current liabilities
* **Debt-to-equity** — total liabilities / total equity
* **Debt-to-assets** — total liabilities / total assets
* **Profit margin** — net income / total revenue
* **Return on assets** (ROA) — net income / total assets
* **Return on equity** (ROE) — net income / total equity
* **Operating margin** — operating income / total revenue
* **Asset turnover** — total revenue / total assets
* **Working capital** — current assets − current liabilities (a $ amount, not a ratio)
* **Current cash ratio** — cash & equivalents / current liabilities

Tags are used to identify "current", "cash", and "inventory" accounts when the
standard account-type heuristics are insufficient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import AccountType
from .ledger import Ledger
from .reports import _ensure_aware, _compute_balances_from_entries


@dataclass
class FinancialRatios:
    """Computed financial ratios and totals."""
    # Absolute totals
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    total_equity: float = 0.0
    total_revenue: float = 0.0
    total_expenses: float = 0.0
    net_income: float = 0.0
    current_assets: float = 0.0
    current_liabilities: float = 0.0
    cash_and_equivalents: float = 0.0
    inventory_value: float = 0.0
    working_capital: float = 0.0

    # Ratios
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    cash_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    debt_to_assets: Optional[float] = None
    profit_margin: Optional[float] = None
    return_on_assets: Optional[float] = None
    return_on_equity: Optional[float] = None
    operating_margin: Optional[float] = None
    asset_turnover: Optional[float] = None

    # Meta
    as_of: Optional[datetime] = None
    warnings: list[str] = field(default_factory=list)


def _classify_account(
    account,
    balance: float,
    *,
    cash_tags: set[str],
    inventory_tags: set[str],
    current_tags: set[str],
) -> tuple[str, float]:
    """Classify an account into a bucket and return (bucket, signed_balance).

    Buckets: asset, liability, equity, revenue, expense,
              current_asset, current_liability, cash, inventory
    """
    atype = account.account_type
    tags = set(account.tags)

    # Tag-based overrides first (more specific)
    if tags & cash_tags:
        return "cash", balance
    if tags & inventory_tags:
        return "inventory", balance
    if tags & current_tags:
        if atype == AccountType.ASSET:
            return "current_asset", balance
        if atype == AccountType.LIABILITY:
            return "current_liability", balance

    return atype.value, balance


def compute_ratios(
    ledger: Ledger,
    as_of: Optional[datetime] = None,
    cash_tags: Optional[set[str]] = None,
    inventory_tags: Optional[set[str]] = None,
    current_tags: Optional[set[str]] = None,
) -> FinancialRatios:
    """Compute financial ratios from the ledger.

    Args:
        ledger: The ledger to analyze.
        as_of: Compute balances as of this date (default: all entries).
        cash_tags: Tags identifying cash & cash-equivalent accounts.
        inventory_tags: Tags identifying inventory accounts.
        current_tags: Tags identifying current (short-term) accounts.

    Returns:
        FinancialRatios dataclass with all computed values.
    """
    cash_tags = cash_tags or {"cash", "liquid", "bank"}
    inventory_tags = inventory_tags or {"inventory", "stock"}
    current_tags = current_tags or {"current", "short-term"}

    # Get balances
    if as_of is not None:
        as_of_aware = _ensure_aware(as_of)
        entries = [
            e for e in ledger.data.entries
            if _ensure_aware(e.timestamp) <= as_of_aware
        ]
        balances = _compute_balances_from_entries(ledger, entries)
    else:
        balances = ledger.get_all_balances()
        as_of_aware = None

    result = FinancialRatios(as_of=as_of)

    for b in balances:
        account = ledger.get_account(b.account_code)
        bucket, amount = _classify_account(
            account, b.balance,
            cash_tags=cash_tags,
            inventory_tags=inventory_tags,
            current_tags=current_tags,
        )

        if bucket == "cash":
            result.cash_and_equivalents += amount
            result.current_assets += amount
            result.total_assets += amount
        elif bucket == "inventory":
            result.inventory_value += amount
            result.current_assets += amount
            result.total_assets += amount
        elif bucket == "current_asset":
            result.current_assets += amount
            result.total_assets += amount
        elif bucket == "current_liability":
            result.current_liabilities += amount
            result.total_liabilities += amount
        elif bucket == "asset":
            result.total_assets += amount
        elif bucket == "liability":
            result.total_liabilities += amount
        elif bucket == "equity":
            result.total_equity += amount
        elif bucket == "revenue":
            result.total_revenue += amount
        elif bucket == "expense":
            result.total_expenses += amount

    # Round totals
    result.total_assets = round(result.total_assets, 2)
    result.total_liabilities = round(result.total_liabilities, 2)
    result.total_equity = round(result.total_equity, 2)
    result.total_revenue = round(result.total_revenue, 2)
    result.total_expenses = round(result.total_expenses, 2)
    result.net_income = round(result.total_revenue - result.total_expenses, 2)
    result.current_assets = round(result.current_assets, 2)
    result.current_liabilities = round(result.current_liabilities, 2)
    result.cash_and_equivalents = round(result.cash_and_equivalents, 2)
    result.inventory_value = round(result.inventory_value, 2)
    result.working_capital = round(result.current_assets - result.current_liabilities, 2)

    # ── Ratios ──────────────────────────────────────────────────

    def _safe_ratio(numerator: float, denominator: float) -> Optional[float]:
        if abs(denominator) < 0.01:
            return None
        return round(numerator / denominator, 4)

    result.current_ratio = _safe_ratio(result.current_assets, result.current_liabilities)
    result.quick_ratio = _safe_ratio(
        result.current_assets - result.inventory_value,
        result.current_liabilities,
    )
    result.cash_ratio = _safe_ratio(result.cash_and_equivalents, result.current_liabilities)
    result.debt_to_equity = _safe_ratio(result.total_liabilities, result.total_equity)
    result.debt_to_assets = _safe_ratio(result.total_liabilities, result.total_assets)
    result.profit_margin = _safe_ratio(result.net_income, result.total_revenue)
    result.return_on_assets = _safe_ratio(result.net_income, result.total_assets)
    result.return_on_equity = _safe_ratio(result.net_income, result.total_equity)
    result.operating_margin = _safe_ratio(
        result.total_revenue - result.total_expenses,
        result.total_revenue,
    )
    result.asset_turnover = _safe_ratio(result.total_revenue, result.total_assets)

    # Warnings for missing data
    if result.total_assets == 0:
        result.warnings.append("No assets found — ratios involving assets will be None.")
    if result.total_revenue == 0:
        result.warnings.append("No revenue found — profitability ratios will be None.")
    if result.current_liabilities == 0:
        result.warnings.append("No current liabilities tagged — liquidity ratios may be None.")

    return result


def format_ratios(ratios: FinancialRatios) -> str:
    """Format financial ratios as a human-readable text report."""
    lines = []
    lines.append("FINANCIAL RATIOS REPORT")
    if ratios.as_of:
        lines.append(f"As of: {ratios.as_of.strftime('%Y-%m-%d')}")
    lines.append("")

    # Totals section
    lines.append("SUMMARY")
    lines.append(f"  {'Total Assets':<30} {ratios.total_assets:>15,.2f}")
    lines.append(f"  {'Total Liabilities':<30} {ratios.total_liabilities:>15,.2f}")
    lines.append(f"  {'Total Equity':<30} {ratios.total_equity:>15,.2f}")
    lines.append(f"  {'Total Revenue':<30} {ratios.total_revenue:>15,.2f}")
    lines.append(f"  {'Total Expenses':<30} {ratios.total_expenses:>15,.2f}")
    lines.append(f"  {'Net Income':<30} {ratios.net_income:>15,.2f}")
    lines.append(f"  {'Current Assets':<30} {ratios.current_assets:>15,.2f}")
    lines.append(f"  {'Current Liabilities':<30} {ratios.current_liabilities:>15,.2f}")
    lines.append(f"  {'Cash & Equivalents':<30} {ratios.cash_and_equivalents:>15,.2f}")
    lines.append(f"  {'Inventory':<30} {ratios.inventory_value:>15,.2f}")
    lines.append(f"  {'Working Capital':<30} {ratios.working_capital:>15,.2f}")
    lines.append("")

    # Ratios section
    lines.append("RATIOS")
    lines.append(f"  {'Current Ratio':<30} {_fmt_ratio(ratios.current_ratio, 'x')}")
    lines.append(f"  {'Quick Ratio (Acid Test)':<30} {_fmt_ratio(ratios.quick_ratio, 'x')}")
    lines.append(f"  {'Cash Ratio':<30} {_fmt_ratio(ratios.cash_ratio, 'x')}")
    lines.append(f"  {'Debt-to-Equity':<30} {_fmt_ratio(ratios.debt_to_equity, 'x')}")
    lines.append(f"  {'Debt-to-Assets':<30} {_fmt_ratio(ratios.debt_to_assets, '%', pct=True)}")
    lines.append(f"  {'Profit Margin':<30} {_fmt_ratio(ratios.profit_margin, '%', pct=True)}")
    lines.append(f"  {'Return on Assets (ROA)':<30} {_fmt_ratio(ratios.return_on_assets, '%', pct=True)}")
    lines.append(f"  {'Return on Equity (ROE)':<30} {_fmt_ratio(ratios.return_on_equity, '%', pct=True)}")
    lines.append(f"  {'Operating Margin':<30} {_fmt_ratio(ratios.operating_margin, '%', pct=True)}")
    lines.append(f"  {'Asset Turnover':<30} {_fmt_ratio(ratios.asset_turnover, 'x')}")

    if ratios.warnings:
        lines.append("")
        lines.append("WARNINGS")
        for w in ratios.warnings:
            lines.append(f"  ⚠ {w}")

    return "\n".join(lines)


def _fmt_ratio(value: Optional[float], unit: str = "", pct: bool = False) -> str:
    """Format a ratio value."""
    if value is None:
        return "N/A"
    if pct:
        return f"{value * 100:.1f}%"
    return f"{value:.2f} {unit}".strip()


def get_financial_health(ratios: FinancialRatios) -> dict:
    """Assess overall financial health based on standard benchmarks.

    Returns a dict with health indicators for liquidity, solvency, and profitability.
    """
    health: dict[str, dict] = {}

    # Liquidity
    if ratios.current_ratio is not None:
        if ratios.current_ratio >= 2.0:
            health["liquidity"] = {"status": "healthy", "current_ratio": ratios.current_ratio}
        elif ratios.current_ratio >= 1.0:
            health["liquidity"] = {"status": "adequate", "current_ratio": ratios.current_ratio}
        else:
            health["liquidity"] = {"status": "at_risk", "current_ratio": ratios.current_ratio}

    # Solvency
    if ratios.debt_to_equity is not None:
        if ratios.debt_to_equity <= 1.0:
            health["solvency"] = {"status": "healthy", "debt_to_equity": ratios.debt_to_equity}
        elif ratios.debt_to_equity <= 2.0:
            health["solvency"] = {"status": "adequate", "debt_to_equity": ratios.debt_to_equity}
        else:
            health["solvency"] = {"status": "at_risk", "debt_to_equity": ratios.debt_to_equity}

    # Profitability
    if ratios.profit_margin is not None:
        if ratios.profit_margin >= 0.15:
            health["profitability"] = {"status": "healthy", "profit_margin": ratios.profit_margin}
        elif ratios.profit_margin >= 0.05:
            health["profitability"] = {"status": "adequate", "profit_margin": ratios.profit_margin}
        elif ratios.profit_margin >= 0:
            health["profitability"] = {"status": "marginal", "profit_margin": ratios.profit_margin}
        else:
            health["profitability"] = {"status": "at_risk", "profit_margin": ratios.profit_margin}

    return health
