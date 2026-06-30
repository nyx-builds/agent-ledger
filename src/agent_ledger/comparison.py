"""Multi-period comparison reports for agent-ledger.

Generates side-by-side period comparisons with variance and percentage change,
enabling trend analysis across time periods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .models import AccountType
from .ledger import Ledger
from .reports import _ensure_aware, _compute_balances_from_entries
from .reports import (
    generate_trial_balance,
    generate_income_statement,
    generate_balance_sheet,
    TrialBalance,
    IncomeStatement,
    BalanceSheet,
)


@dataclass
class PeriodComparisonRow:
    """A single account row in a period comparison."""
    account_code: str
    account_name: str
    account_type: AccountType
    period_values: list[float]  # value in each period
    variance: float = 0.0       # last period - first period
    variance_pct: float = 0.0   # percentage change
    trend: str = "stable"       # "up", "down", "stable", "new", "gone"


@dataclass
class PeriodComparison:
    """Multi-period comparison report."""
    periods: list[str] = field(default_factory=list)  # period labels
    period_ranges: list[dict] = field(default_factory=list)  # {"from": ..., "to": ...}
    rows: list[PeriodComparisonRow] = field(default_factory=list)
    total_rows: list[float] = field(default_factory=list)  # totals per period
    metric: str = "balance"  # what's being compared


@dataclass
class IncomeStatementComparison:
    """Side-by-side income statement comparison."""
    periods: list[str] = field(default_factory=list)
    revenue_rows: list[PeriodComparisonRow] = field(default_factory=list)
    expense_rows: list[PeriodComparisonRow] = field(default_factory=list)
    total_revenue: list[float] = field(default_factory=list)
    total_expenses: list[float] = field(default_factory=list)
    net_income: list[float] = field(default_factory=list)
    revenue_variance: float = 0.0
    revenue_variance_pct: float = 0.0
    expense_variance: float = 0.0
    expense_variance_pct: float = 0.0
    net_income_variance: float = 0.0
    net_income_variance_pct: float = 0.0


def compare_account_balances(
    ledger: Ledger,
    periods: list[tuple[Optional[datetime], Optional[datetime], str]],
) -> PeriodComparison:
    """Compare account balances across multiple time periods.

    Args:
        ledger: The ledger instance
        periods: List of (from_date, to_date, label) tuples.
                 Dates can be None for "all time" bounds.

    Returns:
        PeriodComparison with account values per period

    Example::

        compare_account_balances(ledger, [
            (datetime(2024, 1, 1), datetime(2024, 3, 31), "Q1 2024"),
            (datetime(2024, 4, 1), datetime(2024, 6, 30), "Q2 2024"),
        ])
    """
    if len(periods) < 2:
        raise ValueError("At least 2 periods required for comparison")

    # Compute balances for each period
    period_balances: list[dict[str, tuple[float, float]]] = []
    for from_date, to_date, _ in periods:
        entries = list(ledger.data.entries)
        if from_date is not None:
            fa = _ensure_aware(from_date)
            entries = [e for e in entries if _ensure_aware(e.timestamp) >= fa]
        if to_date is not None:
            ta = _ensure_aware(to_date)
            entries = [e for e in entries if _ensure_aware(e.timestamp) <= ta]
        balances = _compute_balances_from_entries(ledger, entries)

        # Map: code -> (debit_total, credit_total)
        bal_map = {}
        for b in balances:
            bal_map[b.account_code] = (b.debit_total, b.credit_total)
        period_balances.append(bal_map)

    # Collect all account codes across all periods
    accounts = {a.code: a for a in ledger.list_accounts()}
    all_codes = set()
    for pb in period_balances:
        all_codes.update(pb.keys())
    all_codes.update(accounts.keys())

    labels = [p[2] for p in periods]
    rows: list[PeriodComparisonRow] = []

    for code in sorted(all_codes):
        acct = accounts.get(code)
        if acct is None:
            continue

        values = []
        for pb in period_balances:
            dt, ct = pb.get(code, (0.0, 0.0))
            if acct.account_type.is_debit_account:
                val = round(dt - ct, 2)
            else:
                val = round(ct - dt, 2)
            values.append(val)

        first_val = values[0]
        last_val = values[-1]
        variance = round(last_val - first_val, 2)
        if abs(first_val) > 0.01:
            variance_pct = round((variance / abs(first_val)) * 100, 1)
        elif abs(last_val) > 0.01:
            variance_pct = float('inf')  # new activity
        else:
            variance_pct = 0.0

        # Trend
        if first_val == 0 and last_val != 0:
            trend = "new"
        elif first_val != 0 and last_val == 0:
            trend = "gone"
        elif variance > 0.01:
            trend = "up"
        elif variance < -0.01:
            trend = "down"
        else:
            trend = "stable"

        rows.append(PeriodComparisonRow(
            account_code=code,
            account_name=acct.name,
            account_type=acct.account_type,
            period_values=values,
            variance=variance,
            variance_pct=variance_pct,
            trend=trend,
        ))

    # Totals row
    totals = []
    for i in range(len(periods)):
        total = sum(r.period_values[i] for r in rows)
        totals.append(round(total, 2))

    return PeriodComparison(
        periods=labels,
        period_ranges=[
            {"from": p[0].isoformat() if p[0] else None, "to": p[1].isoformat() if p[1] else None}
            for p in periods
        ],
        rows=rows,
        total_rows=totals,
        metric="balance",
    )


def compare_income_statements(
    ledger: Ledger,
    periods: list[tuple[Optional[datetime], Optional[datetime], str]],
) -> IncomeStatementComparison:
    """Compare income statements across multiple periods.

    Args:
        ledger: The ledger instance
        periods: List of (from_date, to_date, label) tuples

    Returns:
        IncomeStatementComparison with revenue/expense/net income per period
    """
    if len(periods) < 2:
        raise ValueError("At least 2 periods required for comparison")

    # Generate income statements per period
    statements: list[IncomeStatement] = []
    for from_date, to_date, _ in periods:
        stmt = generate_income_statement(ledger, from_date, to_date)
        statements.append(stmt)

    labels = [p[2] for p in periods]

    # Build revenue rows
    all_revenue_codes: set[str] = set()
    for stmt in statements:
        all_revenue_codes.update(r.account_code for r in stmt.revenue_rows)
    accounts = {a.code: a for a in ledger.list_accounts()}

    revenue_rows: list[PeriodComparisonRow] = []
    for code in sorted(all_revenue_codes):
        values = []
        for stmt in statements:
            val = 0.0
            for r in stmt.revenue_rows:
                if r.account_code == code:
                    val = r.amount
                    break
            values.append(val)

        acct = accounts.get(code)
        first_val = values[0]
        last_val = values[-1]
        variance = round(last_val - first_val, 2)
        if abs(first_val) > 0.01:
            variance_pct = round((variance / abs(first_val)) * 100, 1)
        elif abs(last_val) > 0.01:
            variance_pct = float('inf')
        else:
            variance_pct = 0.0

        trend = _compute_trend(first_val, last_val)

        revenue_rows.append(PeriodComparisonRow(
            account_code=code,
            account_name=acct.name if acct else code,
            account_type=AccountType.REVENUE,
            period_values=values,
            variance=variance,
            variance_pct=variance_pct,
            trend=trend,
        ))

    # Build expense rows
    all_expense_codes: set[str] = set()
    for stmt in statements:
        all_expense_codes.update(r.account_code for r in stmt.expense_rows)

    expense_rows: list[PeriodComparisonRow] = []
    for code in sorted(all_expense_codes):
        values = []
        for stmt in statements:
            val = 0.0
            for r in stmt.expense_rows:
                if r.account_code == code:
                    val = r.amount
                    break
            values.append(val)

        acct = accounts.get(code)
        first_val = values[0]
        last_val = values[-1]
        variance = round(last_val - first_val, 2)
        if abs(first_val) > 0.01:
            variance_pct = round((variance / abs(first_val)) * 100, 1)
        elif abs(last_val) > 0.01:
            variance_pct = float('inf')
        else:
            variance_pct = 0.0

        trend = _compute_trend(first_val, last_val)

        expense_rows.append(PeriodComparisonRow(
            account_code=code,
            account_name=acct.name if acct else code,
            account_type=AccountType.EXPENSE,
            period_values=values,
            variance=variance,
            variance_pct=variance_pct,
            trend=trend,
        ))

    # Totals
    total_revenue = [round(s.total_revenue, 2) for s in statements]
    total_expenses = [round(s.total_expenses, 2) for s in statements]
    net_income = [round(s.net_income, 2) for s in statements]

    def _var(v1: float, v2: float) -> tuple[float, float]:
        var = round(v2 - v1, 2)
        if abs(v1) > 0.01:
            return var, round((var / abs(v1)) * 100, 1)
        elif abs(v2) > 0.01:
            return var, float('inf')
        return 0.0, 0.0

    rev_var, rev_var_pct = _var(total_revenue[0], total_revenue[-1])
    exp_var, exp_var_pct = _var(total_expenses[0], total_expenses[-1])
    ni_var, ni_var_pct = _var(net_income[0], net_income[-1])

    return IncomeStatementComparison(
        periods=labels,
        revenue_rows=revenue_rows,
        expense_rows=expense_rows,
        total_revenue=total_revenue,
        total_expenses=total_expenses,
        net_income=net_income,
        revenue_variance=rev_var,
        revenue_variance_pct=rev_var_pct,
        expense_variance=exp_var,
        expense_variance_pct=exp_var_pct,
        net_income_variance=ni_var,
        net_income_variance_pct=ni_var_pct,
    )


def _compute_trend(first_val: float, last_val: float) -> str:
    """Compute trend string from first and last values."""
    if first_val == 0 and last_val != 0:
        return "new"
    elif first_val != 0 and last_val == 0:
        return "gone"
    elif last_val > first_val + 0.01:
        return "up"
    elif last_val < first_val - 0.01:
        return "down"
    return "stable"


# ── Formatting ───────────────────────────────────────────────

def format_period_comparison(report: PeriodComparison) -> str:
    """Format a period comparison report as text."""
    lines = []
    lines.append("PERIOD COMPARISON")
    lines.append(f"Periods: {' vs '.join(report.periods)}")
    lines.append("")

    n = len(report.periods)
    col_width = 14
    header = f"{'Code':<12} {'Account':<25}"
    for i in range(n):
        header += f" {report.periods[i]:>{col_width}}"
    header += f" {'Variance':>{col_width}} {'%Change':>10} {'Trend':>8}"
    lines.append(header)
    lines.append("-" * len(header))

    for row in report.rows:
        line = f"{row.account_code:<12} {row.account_name:<25}"
        for v in row.period_values:
            line += f" {v:>{col_width},.2f}"
        var_str = f"{row.variance:>{col_width},.2f}"
        if row.variance_pct == float('inf'):
            pct_str = f"{'∞':>10}"
        else:
            pct_str = f"{row.variance_pct:>+9.1f}%"
        line += f" {var_str} {pct_str} {row.trend:>8}"
        lines.append(line)

    lines.append("-" * len(header))
    line = f"{'TOTAL':<38}"
    for t in report.total_rows:
        line += f" {t:>{col_width},.2f}"
    lines.append(line)

    return "\n".join(lines)


def format_income_statement_comparison(report: IncomeStatementComparison) -> str:
    """Format an income statement comparison as text."""
    lines = []
    lines.append("INCOME STATEMENT COMPARISON")
    lines.append(f"Periods: {' vs '.join(report.periods)}")
    lines.append("")

    n = len(report.periods)
    col_width = 14

    # Revenue section
    lines.append("REVENUE")
    header = f"{'Code':<12} {'Account':<25}"
    for i in range(n):
        header += f" {report.periods[i]:>{col_width}}"
    header += f" {'Variance':>{col_width}} {'%Change':>10}"
    lines.append(header)
    lines.append("-" * len(header))

    for row in report.revenue_rows:
        line = f"{row.account_code:<12} {row.account_name:<25}"
        for v in row.period_values:
            line += f" {v:>{col_width},.2f}"
        line += f" {row.variance:>{col_width},.2f}"
        if row.variance_pct == float('inf'):
            line += f" {'∞':>10}"
        else:
            line += f" {row.variance_pct:>+9.1f}%"
        lines.append(line)

    lines.append("-" * len(header))
    line = f"{'Total Revenue':<37}"
    for v in report.total_revenue:
        line += f" {v:>{col_width},.2f}"
    line += f" {report.revenue_variance:>{col_width},.2f}"
    if report.revenue_variance_pct == float('inf'):
        line += f" {'∞':>10}"
    else:
        line += f" {report.revenue_variance_pct:>+9.1f}%"
    lines.append(line)

    # Expense section
    lines.append("")
    lines.append("EXPENSES")
    lines.append(header)
    lines.append("-" * len(header))

    for row in report.expense_rows:
        line = f"{row.account_code:<12} {row.account_name:<25}"
        for v in row.period_values:
            line += f" {v:>{col_width},.2f}"
        line += f" {row.variance:>{col_width},.2f}"
        if row.variance_pct == float('inf'):
            line += f" {'∞':>10}"
        else:
            line += f" {row.variance_pct:>+9.1f}%"
        lines.append(line)

    lines.append("-" * len(header))
    line = f"{'Total Expenses':<37}"
    for v in report.total_expenses:
        line += f" {v:>{col_width},.2f}"
    line += f" {report.expense_variance:>{col_width},.2f}"
    if report.expense_variance_pct == float('inf'):
        line += f" {'∞':>10}"
    else:
        line += f" {report.expense_variance_pct:>+9.1f}%"
    lines.append(line)

    # Net income
    lines.append("")
    line = f"{'NET INCOME':<37}"
    for v in report.net_income:
        line += f" {v:>{col_width},.2f}"
    line += f" {report.net_income_variance:>{col_width},.2f}"
    if report.net_income_variance_pct == float('inf'):
        line += f" {'∞':>10}"
    else:
        line += f" {report.net_income_variance_pct:>+9.1f}%"
    lines.append(line)

    return "\n".join(lines)
