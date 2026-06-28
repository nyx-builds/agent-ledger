"""Tests for closing entries / period close functionality."""

import pytest
from pathlib import Path
import tempfile

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.closing import close_period
from agent_ledger.exceptions import PeriodCloseError


@pytest.fixture
def ledger_with_activity():
    """Create a ledger with some revenue and expense activity."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "test_ledger.json"
        storage = Storage(filepath)
        ledger = Ledger(storage)
        ledger._data = storage.init(name="Test Ledger")

        # Create accounts
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        ledger.create_account("revenue", "Sales Revenue", AccountType.REVENUE)
        ledger.create_account("expenses", "Office Expenses", AccountType.EXPENSE)
        ledger.create_account("equity", "Owner Equity", AccountType.EQUITY)

        # Post a sale: cash +1000, revenue +1000
        ledger.post_entry(
            "Cash sale",
            lines=[
                JournalLine(account_code="cash", debit=1000.0, credit=0.0),
                JournalLine(account_code="revenue", debit=0.0, credit=1000.0),
            ],
        )

        # Post an expense: cash -300, expenses +300
        ledger.post_entry(
            "Office supplies",
            lines=[
                JournalLine(account_code="expenses", debit=300.0, credit=0.0),
                JournalLine(account_code="cash", debit=0.0, credit=300.0),
            ],
        )

        yield ledger


class TestPeriodClose:
    def test_close_period_basic(self, ledger_with_activity):
        result = close_period(ledger_with_activity)

        assert result.net_income == 700.0  # 1000 - 300
        assert len(result.revenue_accounts_closed) == 1
        assert len(result.expense_accounts_closed) == 1
        assert result.revenue_accounts_closed[0] == "revenue"
        assert result.expense_accounts_closed[0] == "expenses"

    def test_close_period_zeros_temporary_accounts(self, ledger_with_activity):
        close_period(ledger_with_activity)

        # Revenue should be zeroed out
        revenue_balance = ledger_with_activity.get_account_balance("revenue")
        assert revenue_balance.balance == 0.0

        # Expense should be zeroed out
        expense_balance = ledger_with_activity.get_account_balance("expenses")
        assert expense_balance.balance == 0.0

    def test_close_period_adds_to_retained_earnings(self, ledger_with_activity):
        result = close_period(ledger_with_activity)

        # Retained earnings should have net income
        re_balance = ledger_with_activity.get_account_balance("retained_earnings")
        assert re_balance.balance == 700.0

    def test_close_period_creates_retained_earnings_if_missing(self, ledger_with_activity):
        # Don't create retained_earnings manually
        result = close_period(ledger_with_activity, retained_earnings_code="retained_earnings")
        # Should auto-create
        re_account = ledger_with_activity.get_account("retained_earnings")
        assert re_account.account_type == AccountType.EQUITY

    def test_close_period_with_custom_retained_earnings(self, ledger_with_activity):
        ledger_with_activity.create_account("accumulated_e", "Accumulated Earnings", AccountType.EQUITY)
        result = close_period(ledger_with_activity, retained_earnings_code="accumulated_e")

        re_balance = ledger_with_activity.get_account_balance("accumulated_e")
        assert re_balance.balance == 700.0

    def test_close_period_with_custom_description(self, ledger_with_activity):
        result = close_period(ledger_with_activity, description="Q1 2024 Close")
        assert result.closing_entry.description == "Q1 2024 Close"

    def test_close_period_tags_closing_entry(self, ledger_with_activity):
        result = close_period(ledger_with_activity)
        assert "period-close" in result.closing_entry.tags
        assert "closing-entry" in result.closing_entry.tags

    def test_close_period_no_temporary_balances_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test_ledger.json"
            storage = Storage(filepath)
            ledger = Ledger(storage)
            ledger._data = storage.init()

            # Only asset/liability accounts, no activity
            ledger.create_account("cash", "Cash", AccountType.ASSET)
            ledger.create_account("equity", "Equity", AccountType.EQUITY)

            with pytest.raises(PeriodCloseError, match="No temporary accounts"):
                close_period(ledger)

    def test_close_period_with_net_loss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test_ledger.json"
            storage = Storage(filepath)
            ledger = Ledger(storage)
            ledger._data = storage.init()

            ledger.create_account("cash", "Cash", AccountType.ASSET)
            ledger.create_account("expenses", "Expenses", AccountType.EXPENSE)
            ledger.create_account("payable", "Accounts Payable", AccountType.LIABILITY)

            # Expense > Revenue (no revenue)
            ledger.post_entry(
                "Big expense",
                lines=[
                    JournalLine(account_code="expenses", debit=500.0, credit=0.0),
                    JournalLine(account_code="payable", debit=0.0, credit=500.0),
                ],
            )

            result = close_period(ledger)
            assert result.net_income == -500.0

            # Retained earnings should be debited (loss for equity)
            re_balance = ledger.get_account_balance("retained_earnings")
            # For equity account: balance = credit - debit = 0 - 500 = -500
            assert re_balance.balance == -500.0

    def test_close_period_records_in_closed_periods(self, ledger_with_activity):
        close_period(ledger_with_activity)
        closed = ledger_with_activity.get_closed_periods()
        assert len(closed) == 1
        assert closed[0]["net_income"] == 700.0

    def test_close_period_closing_entry_balanced(self, ledger_with_activity):
        result = close_period(ledger_with_activity)
        entry = result.closing_entry
        assert abs(entry.total_debits - entry.total_credits) < 0.01

    def test_close_period_audit_logged(self, ledger_with_activity):
        from agent_ledger.audit import AuditAction
        close_period(ledger_with_activity)
        audit_entries = ledger_with_activity.audit.list_entries(action=AuditAction.PERIOD_CLOSE)
        assert len(audit_entries) == 1
        assert audit_entries[0].details["net_income"] == 700.0

    def test_close_period_retained_earnings_not_equity_raises(self, ledger_with_activity):
        # Create an account with the same code but wrong type
        ledger_with_activity.create_account("my_re", "My RE", AccountType.ASSET)
        with pytest.raises(PeriodCloseError, match="must be an equity account"):
            close_period(ledger_with_activity, retained_earnings_code="my_re")
