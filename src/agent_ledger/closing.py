"""Closing entries and period management for agent-ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import AccountType, JournalLine, JournalEntry, Account
from .ledger import Ledger
from .audit import AuditAction
from .exceptions import LedgerError, PeriodCloseError


class PeriodCloseResult:
    """Result of a period close operation."""

    def __init__(
        self,
        closing_entry: JournalEntry,
        revenue_accounts_closed: list[str],
        expense_accounts_closed: list[str],
        net_income: float,
        retained_earnings_account: str,
        closed_at: datetime,
    ):
        self.closing_entry = closing_entry
        self.revenue_accounts_closed = revenue_accounts_closed
        self.expense_accounts_closed = expense_accounts_closed
        self.net_income = net_income
        self.retained_earnings_account = retained_earnings_account
        self.closed_at = closed_at

    def __repr__(self) -> str:
        return (
            f"PeriodCloseResult("
            f"revenue_closed={len(self.revenue_accounts_closed)}, "
            f"expense_closed={len(self.expense_accounts_closed)}, "
            f"net_income={self.net_income:.2f}, "
            f"retained_earnings={self.retained_earnings_account})"
        )


def close_period(
    ledger: Ledger,
    retained_earnings_code: str = "retained_earnings",
    description: Optional[str] = None,
    close_date: Optional[datetime] = None,
) -> PeriodCloseResult:
    """Close the current accounting period.

    This creates a closing entry that:
    1. Debits all revenue accounts to zero them out
    2. Credits all expense accounts to zero them out
    3. Credits/Debits retained earnings for the net income/loss

    Args:
        ledger: The ledger to close
        retained_earnings_code: Account code for retained earnings (created if not exists)
        description: Custom description for the closing entry
        close_date: Date for the closing entry (defaults to now)

    Returns:
        PeriodCloseResult with details about the close

    Raises:
        PeriodCloseError: If the close cannot be performed
    """
    # Ensure retained earnings account exists
    try:
        re_account = ledger.get_account(retained_earnings_code)
        if re_account.account_type != AccountType.EQUITY:
            raise PeriodCloseError(
                f"Account '{retained_earnings_code}' must be an equity account, "
                f"but is {re_account.account_type.value}"
            )
    except Exception as e:
        if "not found" in str(e).lower():
            # Create the retained earnings account
            re_account = ledger.create_account(
                code=retained_earnings_code,
                name="Retained Earnings",
                account_type=AccountType.EQUITY,
                description="Accumulated retained earnings from period closes",
            )
        else:
            raise

    # Get all revenue and expense balances
    lines: list[JournalLine] = []
    revenue_closed: list[str] = []
    expense_closed: list[str] = []
    total_revenue = 0.0
    total_expenses = 0.0

    for account in ledger.list_accounts():
        if account.account_type == AccountType.REVENUE:
            balance = ledger.get_account_balance(account.code)
            if balance.balance != 0:
                # Revenue has a normal credit balance
                # To close: debit revenue (to zero it)
                lines.append(JournalLine(
                    account_code=account.code,
                    debit=balance.balance,
                    credit=0.0,
                    description=f"Close revenue: {account.name}",
                ))
                revenue_closed.append(account.code)
                total_revenue += balance.balance

        elif account.account_type == AccountType.EXPENSE:
            balance = ledger.get_account_balance(account.code)
            if balance.balance != 0:
                # Expense has a normal debit balance
                # To close: credit expense (to zero it)
                lines.append(JournalLine(
                    account_code=account.code,
                    debit=0.0,
                    credit=balance.balance,
                    description=f"Close expense: {account.name}",
                ))
                expense_closed.append(account.code)
                total_expenses += balance.balance

    if not lines:
        raise PeriodCloseError("No temporary accounts with non-zero balances to close")

    net_income = total_revenue - total_expenses

    # Add retained earnings line to balance the entry
    if net_income > 0:
        # Net income: credit retained earnings
        lines.append(JournalLine(
            account_code=retained_earnings_code,
            debit=0.0,
            credit=net_income,
            description="Net income to retained earnings",
        ))
    elif net_income < 0:
        # Net loss: debit retained earnings
        lines.append(JournalLine(
            account_code=retained_earnings_code,
            debit=abs(net_income),
            credit=0.0,
            description="Net loss to retained earnings",
        ))
    else:
        # Exactly balanced — still need to balance the entry
        # This shouldn't normally happen with non-zero accounts but handle it
        pass

    # Post the closing entry
    closing_entry = ledger.post_entry(
        description=description or "Period close — closing temporary accounts",
        lines=lines,
        tags=["period-close", "closing-entry"],
        timestamp=close_date or datetime.now(timezone.utc),
    )

    # Record the closed period in the ledger
    ledger.record_closed_period({
        "closing_entry_id": closing_entry.id,
        "revenue_accounts_closed": revenue_closed,
        "expense_accounts_closed": expense_closed,
        "net_income": net_income,
        "retained_earnings_account": retained_earnings_code,
        "closed_at": closing_entry.timestamp.isoformat(),
    })

    # Audit the period close
    ledger.audit.log(
        action=AuditAction.PERIOD_CLOSE,
        details={
            "closing_entry_id": closing_entry.id,
            "net_income": net_income,
            "revenue_closed": len(revenue_closed),
            "expense_closed": len(expense_closed),
        },
    )

    return PeriodCloseResult(
        closing_entry=closing_entry,
        revenue_accounts_closed=revenue_closed,
        expense_accounts_closed=expense_closed,
        net_income=net_income,
        retained_earnings_account=retained_earnings_code,
        closed_at=closing_entry.timestamp,
    )
