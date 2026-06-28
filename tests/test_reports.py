"""Tests for agent-ledger financial reports."""

import pytest

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.reports import (
    generate_trial_balance, generate_income_statement, generate_balance_sheet,
    format_trial_balance, format_income_statement, format_balance_sheet,
)


@pytest.fixture
def populated_ledger(tmp_path):
    """Create a ledger with accounts and entries for report testing."""
    filepath = tmp_path / "ledger.json"
    storage = Storage(filepath)
    storage.init()
    ledger = Ledger(storage)

    # Create accounts
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("bank", "Bank Account", AccountType.ASSET)
    ledger.create_account("inventory", "Inventory", AccountType.ASSET)
    ledger.create_account("ap", "Accounts Payable", AccountType.LIABILITY)
    ledger.create_account("equity", "Owner's Equity", AccountType.EQUITY)
    ledger.create_account("revenue", "Sales Revenue", AccountType.REVENUE)
    ledger.create_account("expenses", "Operating Expenses", AccountType.EXPENSE)

    # Post entries
    ledger.post_entry(
        "Owner investment",
        [
            JournalLine(account_code="cash", debit=10000.0),
            JournalLine(account_code="equity", credit=10000.0),
        ],
    )
    ledger.post_entry(
        "Cash sale",
        [
            JournalLine(account_code="cash", debit=5000.0),
            JournalLine(account_code="revenue", credit=5000.0),
        ],
    )
    ledger.post_entry(
        "Pay expenses",
        [
            JournalLine(account_code="expenses", debit=2000.0),
            JournalLine(account_code="cash", credit=2000.0),
        ],
    )
    ledger.post_entry(
        "Buy inventory on credit",
        [
            JournalLine(account_code="inventory", debit=3000.0),
            JournalLine(account_code="ap", credit=3000.0),
        ],
    )

    return ledger


class TestTrialBalance:
    """Test trial balance report generation."""

    def test_trial_balance_balanced(self, populated_ledger):
        tb = generate_trial_balance(populated_ledger)
        assert tb.is_balanced

    def test_trial_balance_totals(self, populated_ledger):
        tb = generate_trial_balance(populated_ledger)
        # All debits should equal all credits
        assert tb.total_debits == tb.total_credits

    def test_trial_balance_rows(self, populated_ledger):
        tb = generate_trial_balance(populated_ledger)
        # Should have rows for accounts with non-zero balance
        assert len(tb.rows) > 0

    def test_trial_balance_cash(self, populated_ledger):
        tb = generate_trial_balance(populated_ledger)
        cash_row = next(r for r in tb.rows if r.account_code == "cash")
        # Cash: debit 15000, credit 2000 = debit balance 13000
        assert cash_row.debit == 13000.0
        assert cash_row.credit == 0.0

    def test_trial_balance_format(self, populated_ledger):
        tb = generate_trial_balance(populated_ledger)
        text = format_trial_balance(tb)
        assert "TRIAL BALANCE" in text
        assert "Balanced: Yes" in text


class TestIncomeStatement:
    """Test income statement report generation."""

    def test_income_statement_totals(self, populated_ledger):
        ist = generate_income_statement(populated_ledger)
        assert ist.total_revenue == 5000.0
        assert ist.total_expenses == 2000.0
        assert ist.net_income == 3000.0

    def test_income_statement_rows(self, populated_ledger):
        ist = generate_income_statement(populated_ledger)
        assert len(ist.revenue_rows) == 1
        assert len(ist.expense_rows) == 1

    def test_income_statement_format(self, populated_ledger):
        ist = generate_income_statement(populated_ledger)
        text = format_income_statement(ist)
        assert "INCOME STATEMENT" in text
        assert "NET INCOME" in text


class TestBalanceSheet:
    """Test balance sheet report generation."""

    def test_balance_sheet_totals(self, populated_ledger):
        bs = generate_balance_sheet(populated_ledger)
        assert bs.total_assets == 16000.0  # cash 13000 + inventory 3000
        assert bs.total_liabilities == 3000.0  # ap
        assert bs.total_equity == 10000.0  # equity
        assert bs.retained_earnings == 3000.0  # net income

    def test_balance_sheet_balanced(self, populated_ledger):
        bs = generate_balance_sheet(populated_ledger)
        total_le = bs.total_liabilities + bs.total_equity + bs.retained_earnings
        assert abs(bs.total_assets - total_le) < 0.01

    def test_balance_sheet_assets(self, populated_ledger):
        bs = generate_balance_sheet(populated_ledger)
        assert len(bs.assets) == 2  # cash and inventory

    def test_balance_sheet_format(self, populated_ledger):
        bs = generate_balance_sheet(populated_ledger)
        text = format_balance_sheet(bs)
        assert "BALANCE SHEET" in text
        assert "Balanced" in text


class TestEmptyReports:
    """Test reports with no data."""

    def test_empty_trial_balance(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        tb = generate_trial_balance(ledger)
        assert tb.total_debits == 0.0
        assert tb.total_credits == 0.0
        assert tb.is_balanced

    def test_empty_income_statement(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        ist = generate_income_statement(ledger)
        assert ist.net_income == 0.0

    def test_empty_balance_sheet(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        bs = generate_balance_sheet(ledger)
        assert bs.total_assets == 0.0
