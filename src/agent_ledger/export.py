"""CSV export for agent-ledger — export accounts, entries, and reports."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from .ledger import Ledger
from .reports import (
    generate_trial_balance, generate_income_statement, generate_balance_sheet,
    TrialBalance, IncomeStatement, BalanceSheet,
)
from .hierarchy import AccountHierarchy


def export_accounts_csv(ledger: Ledger, include_balances: bool = True) -> str:
    """Export chart of accounts as CSV.

    Args:
        ledger: The ledger to export from
        include_balances: Whether to include balance columns

    Returns:
        CSV string
    """
    output = io.StringIO()
    writer = csv.writer(output)

    if include_balances:
        writer.writerow([
            "code", "name", "type", "currency", "active",
            "parent_code", "balance", "raw_balance",
            "debit_total", "credit_total",
        ])
    else:
        writer.writerow([
            "code", "name", "type", "currency", "active", "parent_code",
        ])

    for account in ledger.list_accounts():
        if include_balances:
            balance = ledger.get_account_balance(account.code)
            writer.writerow([
                account.code,
                account.name,
                account.account_type.value,
                account.currency,
                account.active,
                account.parent_code or "",
                balance.balance,
                balance.raw_balance,
                balance.debit_total,
                balance.credit_total,
            ])
        else:
            writer.writerow([
                account.code,
                account.name,
                account.account_type.value,
                account.currency,
                account.active,
                account.parent_code or "",
            ])

    return output.getvalue()


def export_entries_csv(
    ledger: Ledger,
    account_code: Optional[str] = None,
    tag: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> str:
    """Export journal entries as CSV.

    Each row is one journal line (entry-level data is repeated per line).

    Args:
        ledger: The ledger to export from
        account_code: Optional filter by account
        tag: Optional filter by tag
        start_date: Optional start date filter
        end_date: Optional end date filter

    Returns:
        CSV string
    """
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "entry_id", "entry_description", "entry_timestamp", "entry_tags",
        "entry_reconciled", "line_account_code", "line_debit", "line_credit",
        "line_description",
    ])

    entries = ledger.list_entries(
        account_code=account_code,
        tag=tag,
        start_date=start_date,
        end_date=end_date,
    )

    for entry in entries:
        tags_str = ";".join(entry.tags)
        timestamp_str = entry.timestamp.isoformat()
        for line in entry.lines:
            writer.writerow([
                entry.id,
                entry.description,
                timestamp_str,
                tags_str,
                entry.reconciled,
                line.account_code,
                line.debit,
                line.credit,
                line.description,
            ])

    return output.getvalue()


def export_trial_balance_csv(ledger: Ledger) -> str:
    """Export trial balance as CSV."""
    report = generate_trial_balance(ledger)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["account_code", "account_name", "account_type", "debit", "credit"])
    for row in report.rows:
        writer.writerow([
            row.account_code,
            row.account_name,
            row.account_type.value,
            row.debit,
            row.credit,
        ])
    writer.writerow(["", "TOTAL", "", report.total_debits, report.total_credits])

    return output.getvalue()


def export_income_statement_csv(ledger: Ledger) -> str:
    """Export income statement as CSV."""
    report = generate_income_statement(ledger)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["section", "account_code", "account_name", "amount"])

    writer.writerow(["REVENUE", "", "", ""])
    for row in report.revenue_rows:
        writer.writerow(["revenue", row.account_code, row.account_name, row.amount])
    writer.writerow(["revenue", "", "Total Revenue", report.total_revenue])

    writer.writerow(["EXPENSES", "", "", ""])
    for row in report.expense_rows:
        writer.writerow(["expense", row.account_code, row.account_name, row.amount])
    writer.writerow(["expense", "", "Total Expenses", report.total_expenses])

    writer.writerow(["net_income", "", "Net Income", report.net_income])

    return output.getvalue()


def export_balance_sheet_csv(ledger: Ledger) -> str:
    """Export balance sheet as CSV."""
    report = generate_balance_sheet(ledger)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["section", "account_code", "account_name", "amount"])

    writer.writerow(["ASSETS", "", "", ""])
    for row in report.assets:
        writer.writerow(["asset", row.account_code, row.account_name, row.amount])
    writer.writerow(["asset", "", "Total Assets", report.total_assets])

    writer.writerow(["LIABILITIES", "", "", ""])
    for row in report.liabilities:
        writer.writerow(["liability", row.account_code, row.account_name, row.amount])
    writer.writerow(["liability", "", "Total Liabilities", report.total_liabilities])

    writer.writerow(["EQUITY", "", "", ""])
    for row in report.equity_rows:
        writer.writerow(["equity", row.account_code, row.account_name, row.amount])
    writer.writerow(["equity", "", "Retained Earnings", report.retained_earnings])
    writer.writerow(["equity", "", "Total Equity", report.total_equity])

    total_le = round(report.total_liabilities + report.total_equity + report.retained_earnings, 2)
    writer.writerow(["total", "", "Total Liabilities + Equity", total_le])

    return output.getvalue()


def export_account_transactions_csv(ledger: Ledger, account_code: str) -> str:
    """Export transactions for a single account as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "entry_id", "timestamp", "description", "debit", "credit", "balance", "reconciled",
    ])

    transactions = ledger.get_account_transactions(account_code)
    for tx in transactions:
        writer.writerow([
            tx["entry_id"],
            tx["timestamp"].isoformat(),
            tx["description"],
            tx["debit"],
            tx["credit"],
            tx["balance"],
            tx["reconciled"],
        ])

    return output.getvalue()


def export_hierarchy_csv(ledger: Ledger) -> str:
    """Export account hierarchy with rollup balances as CSV."""
    hierarchy = AccountHierarchy(ledger)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "code", "name", "type", "parent_code", "depth",
        "own_balance", "rollup_balance",
        "is_leaf", "is_root",
    ])

    for account in ledger.list_accounts():
        depth = hierarchy.get_depth(account.code)
        balance = ledger.get_account_balance(account.code)
        rollup = hierarchy.get_rollup_balance(account.code)

        writer.writerow([
            account.code,
            account.name,
            account.account_type.value,
            account.parent_code or "",
            depth,
            balance.balance,
            rollup.balance,
            hierarchy.is_leaf(account.code),
            hierarchy.is_root(account.code),
        ])

    return output.getvalue()


def write_csv_to_file(csv_content: str, filepath: str) -> None:
    """Write CSV content to a file."""
    from pathlib import Path
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(csv_content, encoding="utf-8")
