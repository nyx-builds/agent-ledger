"""General Ledger report for agent-ledger — detailed journal with running balances."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .models import AccountType
from .ledger import Ledger
from .reports import _ensure_aware


@dataclass
class GLLine:
    """A single line in the General Ledger report."""
    entry_id: str
    timestamp: datetime
    account_code: str
    account_name: str
    account_type: AccountType
    description: str
    debit: float
    credit: float
    running_balance: float


@dataclass
class GeneralLedgerReport:
    """General Ledger report — all transactions with running balances."""
    lines: list[GLLine] = field(default_factory=list)
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    account_code: Optional[str] = None
    total_debits: float = 0.0
    total_credits: float = 0.0
    total_entries: int = 0


def generate_general_ledger(
    ledger: Ledger,
    account_code: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    tag: Optional[str] = None,
) -> GeneralLedgerReport:
    """Generate a General Ledger report.

    Produces a detailed listing of all transactions with running balances,
    optionally filtered by account, date range, or tag.

    Args:
        ledger: The ledger to generate the report from
        account_code: Optional account code to filter by
        from_date: Optional start date
        to_date: Optional end date
        tag: Optional tag filter

    Returns:
        GeneralLedgerReport with all transaction lines and running balances
    """
    entries = list(ledger.data.entries)

    # Filter by date
    if from_date is not None:
        from_aware = _ensure_aware(from_date)
        entries = [e for e in entries if _ensure_aware(e.timestamp) >= from_aware]
    if to_date is not None:
        to_aware = _ensure_aware(to_date)
        entries = [e for e in entries if _ensure_aware(e.timestamp) <= to_aware]

    # Filter by tag
    if tag is not None:
        entries = [e for e in entries if tag in e.tags]

    # Sort by timestamp
    entries = sorted(entries, key=lambda e: e.timestamp)

    # Track running balances per account
    running_balances: dict[str, float] = {}
    accounts_map = {a.code: a for a in ledger.list_accounts()}

    # Initialize from existing balances before from_date
    if from_date is not None:
        from_aware = _ensure_aware(from_date)
        prior_entries = [e for e in ledger.data.entries if _ensure_aware(e.timestamp) < from_aware]
        for entry in sorted(prior_entries, key=lambda e: e.timestamp):
            for line in entry.lines:
                if line.account_code not in running_balances:
                    running_balances[line.account_code] = 0.0
                account = accounts_map.get(line.account_code)
                if account and account.account_type.is_debit_account:
                    running_balances[line.account_code] += line.debit - line.credit
                else:
                    running_balances[line.account_code] += line.credit - line.debit
    else:
        for code in accounts_map:
            running_balances[code] = 0.0

    gl_lines = []
    total_debits = 0.0
    total_credits = 0.0
    seen_entry_ids = set()

    for entry in entries:
        for line in entry.lines:
            # Filter by account if specified
            if account_code is not None:
                code = account_code.strip().lower()
                if line.account_code != code:
                    continue

            account = accounts_map.get(line.account_code)
            if account is None:
                continue

            # Update running balance
            if line.account_code not in running_balances:
                running_balances[line.account_code] = 0.0

            if account.account_type.is_debit_account:
                running_balances[line.account_code] += line.debit - line.credit
            else:
                running_balances[line.account_code] += line.credit - line.debit

            gl_lines.append(GLLine(
                entry_id=entry.id,
                timestamp=entry.timestamp,
                account_code=line.account_code,
                account_name=account.name,
                account_type=account.account_type,
                description=entry.description,
                debit=line.debit,
                credit=line.credit,
                running_balance=round(running_balances[line.account_code], 2),
            ))

            total_debits += line.debit
            total_credits += line.credit

        seen_entry_ids.add(entry.id)

    return GeneralLedgerReport(
        lines=gl_lines,
        from_date=from_date,
        to_date=to_date,
        account_code=account_code,
        total_debits=round(total_debits, 2),
        total_credits=round(total_credits, 2),
        total_entries=len(seen_entry_ids),
    )


def format_general_ledger(report: GeneralLedgerReport) -> str:
    """Format a General Ledger report as text."""
    lines = []
    lines.append("GENERAL LEDGER")
    if report.account_code:
        lines.append(f"Account: {report.account_code}")
    if report.from_date and report.to_date:
        lines.append(
            f"Period: {report.from_date.strftime('%Y-%m-%d')} to "
            f"{report.to_date.strftime('%Y-%m-%d')}"
        )
    lines.append("")

    lines.append(
        f"{'Date':<12} {'Entry':<10} {'Account':<12} {'Description':<25} "
        f"{'Debit':>12} {'Credit':>12} {'Balance':>12}"
    )
    lines.append("-" * 97)

    for gl in report.lines:
        date_str = gl.timestamp.strftime("%Y-%m-%d")
        entry_str = gl.entry_id[:8]
        debit_str = f"{gl.debit:,.2f}" if gl.debit else ""
        credit_str = f"{gl.credit:,.2f}" if gl.credit else ""

        lines.append(
            f"{date_str:<12} {entry_str:<10} {gl.account_code:<12} "
            f"{gl.description[:25]:<25} {debit_str:>12} {credit_str:>12} "
            f"{gl.running_balance:>12,.2f}"
        )

    lines.append("-" * 97)
    lines.append(
        f"{'TOTALS':<59} {report.total_debits:>12,.2f} {report.total_credits:>12,.2f}"
    )
    lines.append(f"\nTotal Entries: {report.total_entries}")

    return "\n".join(lines)
