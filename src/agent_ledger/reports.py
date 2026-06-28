"""Financial report generators for agent-ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .models import AccountType, AccountBalance
from .ledger import Ledger


@dataclass
class TrialBalanceRow:
    """A row in a trial balance report."""
    account_code: str
    account_name: str
    account_type: AccountType
    debit: float
    credit: float


@dataclass
class TrialBalance:
    """Trial balance report."""
    rows: list[TrialBalanceRow] = field(default_factory=list)
    total_debits: float = 0.0
    total_credits: float = 0.0
    as_of: Optional[datetime] = None

    @property
    def is_balanced(self) -> bool:
        return abs(self.total_debits - self.total_credits) < 0.01


@dataclass
class IncomeStatementRow:
    """A row in an income statement."""
    account_code: str
    account_name: str
    amount: float


@dataclass
class IncomeStatement:
    """Income statement report."""
    revenue_rows: list[IncomeStatementRow] = field(default_factory=list)
    expense_rows: list[IncomeStatementRow] = field(default_factory=list)
    total_revenue: float = 0.0
    total_expenses: float = 0.0
    net_income: float = 0.0
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None


@dataclass
class BalanceSheetRow:
    """A row in a balance sheet."""
    account_code: str
    account_name: str
    account_type: AccountType
    amount: float


@dataclass
class BalanceSheet:
    """Balance sheet report."""
    assets: list[BalanceSheetRow] = field(default_factory=list)
    liabilities: list[BalanceSheetRow] = field(default_factory=list)
    equity_rows: list[BalanceSheetRow] = field(default_factory=list)
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    total_equity: float = 0.0
    retained_earnings: float = 0.0
    as_of: Optional[datetime] = None


def generate_trial_balance(ledger: Ledger, as_of: Optional[datetime] = None) -> TrialBalance:
    """Generate a trial balance report.

    Lists all accounts with their debit or credit balance.
    Debit-balance accounts show under Debit column.
    Credit-balance accounts show under Credit column.
    """
    balances = ledger.get_all_balances()
    rows = []
    total_debits = 0.0
    total_credits = 0.0

    for b in sorted(balances, key=lambda x: x.account_code):
        # For trial balance: show debit balance in debit column, credit balance in credit column
        raw = b.raw_balance
        if raw > 0:
            debit = raw
            credit = 0.0
        elif raw < 0:
            debit = 0.0
            credit = abs(raw)
        else:
            debit = 0.0
            credit = 0.0

        rows.append(TrialBalanceRow(
            account_code=b.account_code,
            account_name=b.account_name,
            account_type=b.account_type,
            debit=round(debit, 2),
            credit=round(credit, 2),
        ))
        total_debits += debit
        total_credits += credit

    return TrialBalance(
        rows=rows,
        total_debits=round(total_debits, 2),
        total_credits=round(total_credits, 2),
        as_of=as_of,
    )


def generate_income_statement(
    ledger: Ledger,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> IncomeStatement:
    """Generate an income statement (profit & loss).

    Shows revenue and expense accounts with their balances.
    Net income = total revenue - total expenses.
    """
    balances = ledger.get_all_balances()

    revenue_rows = []
    expense_rows = []
    total_revenue = 0.0
    total_expenses = 0.0

    for b in sorted(balances, key=lambda x: x.account_code):
        if b.account_type == AccountType.REVENUE and b.balance != 0:
            revenue_rows.append(IncomeStatementRow(
                account_code=b.account_code,
                account_name=b.account_name,
                amount=b.balance,
            ))
            total_revenue += b.balance

        elif b.account_type == AccountType.EXPENSE and b.balance != 0:
            expense_rows.append(IncomeStatementRow(
                account_code=b.account_code,
                account_name=b.account_name,
                amount=b.balance,
            ))
            total_expenses += b.balance

    return IncomeStatement(
        revenue_rows=revenue_rows,
        expense_rows=expense_rows,
        total_revenue=round(total_revenue, 2),
        total_expenses=round(total_expenses, 2),
        net_income=round(total_revenue - total_expenses, 2),
        from_date=from_date,
        to_date=to_date,
    )


def generate_balance_sheet(
    ledger: Ledger,
    as_of: Optional[datetime] = None,
) -> BalanceSheet:
    """Generate a balance sheet.

    Assets = Liabilities + Equity + Retained Earnings (Net Income)
    """
    balances = ledger.get_all_balances()
    income_statement = generate_income_statement(ledger)

    assets = []
    liabilities = []
    equity_rows = []
    total_assets = 0.0
    total_liabilities = 0.0
    total_equity = 0.0

    for b in sorted(balances, key=lambda x: x.account_code):
        if b.account_type == AccountType.ASSET and b.balance != 0:
            assets.append(BalanceSheetRow(
                account_code=b.account_code,
                account_name=b.account_name,
                account_type=b.account_type,
                amount=b.balance,
            ))
            total_assets += b.balance

        elif b.account_type == AccountType.LIABILITY and b.balance != 0:
            liabilities.append(BalanceSheetRow(
                account_code=b.account_code,
                account_name=b.account_name,
                account_type=b.account_type,
                amount=b.balance,
            ))
            total_liabilities += b.balance

        elif b.account_type == AccountType.EQUITY and b.balance != 0:
            equity_rows.append(BalanceSheetRow(
                account_code=b.account_code,
                account_name=b.account_name,
                account_type=b.account_type,
                amount=b.balance,
            ))
            total_equity += b.balance

    return BalanceSheet(
        assets=assets,
        liabilities=liabilities,
        equity_rows=equity_rows,
        total_assets=round(total_assets, 2),
        total_liabilities=round(total_liabilities, 2),
        total_equity=round(total_equity, 2),
        retained_earnings=income_statement.net_income,
        as_of=as_of,
    )


def format_trial_balance(report: TrialBalance) -> str:
    """Format a trial balance as a text table."""
    lines = []
    lines.append("TRIAL BALANCE")
    if report.as_of:
        lines.append(f"As of: {report.as_of.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append(f"{'Code':<12} {'Account':<30} {'Debit':>12} {'Credit':>12}")
    lines.append("-" * 68)

    for row in report.rows:
        debit_str = f"{row.debit:,.2f}" if row.debit else ""
        credit_str = f"{row.credit:,.2f}" if row.credit else ""
        lines.append(
            f"{row.account_code:<12} {row.account_name:<30} {debit_str:>12} {credit_str:>12}"
        )

    lines.append("-" * 68)
    lines.append(
        f"{'TOTAL':<42} {report.total_debits:>12,.2f} {report.total_credits:>12,.2f}"
    )
    lines.append(f"\nBalanced: {'Yes' if report.is_balanced else 'No'}")
    return "\n".join(lines)


def format_income_statement(report: IncomeStatement) -> str:
    """Format an income statement as text."""
    lines = []
    lines.append("INCOME STATEMENT")
    date_range = ""
    if report.from_date and report.to_date:
        date_range = f"{report.from_date.strftime('%Y-%m-%d')} to {report.to_date.strftime('%Y-%m-%d')}"
    if date_range:
        lines.append(f"Period: {date_range}")
    lines.append("")

    lines.append("REVENUE")
    lines.append(f"{'Code':<12} {'Account':<30} {'Amount':>12}")
    lines.append("-" * 56)
    for row in report.revenue_rows:
        lines.append(f"{row.account_code:<12} {row.account_name:<30} {row.amount:>12,.2f}")
    lines.append(f"{'Total Revenue':<42} {report.total_revenue:>12,.2f}")

    lines.append("")
    lines.append("EXPENSES")
    lines.append(f"{'Code':<12} {'Account':<30} {'Amount':>12}")
    lines.append("-" * 56)
    for row in report.expense_rows:
        lines.append(f"{row.account_code:<12} {row.account_name:<30} {row.amount:>12,.2f}")
    lines.append(f"{'Total Expenses':<42} {report.total_expenses:>12,.2f}")

    lines.append("")
    lines.append(f"{'NET INCOME':<42} {report.net_income:>12,.2f}")
    return "\n".join(lines)


def format_balance_sheet(report: BalanceSheet) -> str:
    """Format a balance sheet as text."""
    lines = []
    lines.append("BALANCE SHEET")
    if report.as_of:
        lines.append(f"As of: {report.as_of.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    lines.append("ASSETS")
    lines.append(f"{'Code':<12} {'Account':<30} {'Amount':>12}")
    lines.append("-" * 56)
    for row in report.assets:
        lines.append(f"{row.account_code:<12} {row.account_name:<30} {row.amount:>12,.2f}")
    lines.append(f"{'Total Assets':<42} {report.total_assets:>12,.2f}")

    lines.append("")
    lines.append("LIABILITIES")
    lines.append(f"{'Code':<12} {'Account':<30} {'Amount':>12}")
    lines.append("-" * 56)
    for row in report.liabilities:
        lines.append(f"{row.account_code:<12} {row.account_name:<30} {row.amount:>12,.2f}")
    lines.append(f"{'Total Liabilities':<42} {report.total_liabilities:>12,.2f}")

    lines.append("")
    lines.append("EQUITY")
    lines.append(f"{'Code':<12} {'Account':<30} {'Amount':>12}")
    lines.append("-" * 56)
    for row in report.equity_rows:
        lines.append(f"{row.account_code:<12} {row.account_name:<30} {row.amount:>12,.2f}")
    lines.append(f"{'Retained Earnings':<42} {report.retained_earnings:>12,.2f}")
    total_le = round(report.total_liabilities + report.total_equity + report.retained_earnings, 2)
    lines.append(f"{'Total Liabilities + Equity':<42} {total_le:>12,.2f}")

    lines.append("")
    balanced = abs(report.total_assets - total_le) < 0.01
    lines.append(f"Balanced (Assets = L + E): {'Yes' if balanced else 'No'}")
    return "\n".join(lines)
