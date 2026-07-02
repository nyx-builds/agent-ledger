"""Tests for cash flow statement generation."""

import pytest

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.cashflow import (
    generate_cash_flow_statement, format_cash_flow_statement,
    _is_cash_account,
)


@pytest.fixture
def ledger_with_flows(tmp_path):
    """Create a ledger with typical business transactions."""
    filepath = tmp_path / "ledger.json"
    storage = Storage(filepath)
    storage.init(name="Cash Flow Test")
    ledger = Ledger(storage)

    # Create accounts
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("bank", "Bank Account", AccountType.ASSET)
    ledger.create_account("inventory", "Inventory", AccountType.ASSET)
    ledger.create_account("equipment", "Equipment", AccountType.ASSET)
    ledger.create_account("ap", "Accounts Payable", AccountType.LIABILITY)
    ledger.create_account("loan", "Bank Loan", AccountType.LIABILITY)
    ledger.create_account("equity", "Owner's Equity", AccountType.EQUITY)
    ledger.create_account("revenue", "Sales Revenue", AccountType.REVENUE)
    ledger.create_account("expenses", "Operating Expenses", AccountType.EXPENSE)

    # Owner investment (financing)
    ledger.post_entry(
        "Owner investment",
        [
            JournalLine(account_code="cash", debit=10000.0),
            JournalLine(account_code="equity", credit=10000.0),
        ],
    )

    # Cash sale (operating)
    ledger.post_entry(
        "Cash sale",
        [
            JournalLine(account_code="cash", debit=5000.0),
            JournalLine(account_code="revenue", credit=5000.0),
        ],
    )

    # Pay expenses (operating)
    ledger.post_entry(
        "Pay expenses",
        [
            JournalLine(account_code="expenses", debit=2000.0),
            JournalLine(account_code="cash", credit=2000.0),
        ],
    )

    # Buy equipment (investing)
    ledger.post_entry(
        "Buy equipment",
        [
            JournalLine(account_code="equipment", debit=3000.0),
            JournalLine(account_code="cash", credit=3000.0),
        ],
    )

    # Get a loan (financing)
    ledger.post_entry(
        "Get bank loan",
        [
            JournalLine(account_code="cash", debit=5000.0),
            JournalLine(account_code="loan", credit=5000.0),
        ],
    )

    return ledger


class TestCashFlowGeneration:
    """Test cash flow statement generation."""

    def test_operating_activities(self, ledger_with_flows):
        cf = generate_cash_flow_statement(ledger_with_flows)
        assert len(cf.operating.items) == 2  # revenue + expenses
        # Revenue = 5000 (positive), Expenses = -2000
        assert cf.operating.total == 3000.0

    def test_investing_activities(self, ledger_with_flows):
        cf = generate_cash_flow_statement(ledger_with_flows)
        # Equipment = -3000 (increase in asset = cash outflow)
        assert len(cf.investing.items) == 1
        assert cf.investing.total == -3000.0

    def test_financing_activities(self, ledger_with_flows):
        cf = generate_cash_flow_statement(ledger_with_flows)
        # Equity = 10000 (financing inflow), Loan = 5000 (financing inflow)
        assert len(cf.financing.items) == 2
        assert cf.financing.total == 15000.0

    def test_net_change_in_cash(self, ledger_with_flows):
        cf = generate_cash_flow_statement(ledger_with_flows)
        # 3000 (operating) + (-3000) (investing) + 15000 (financing) = 15000
        assert cf.net_change_in_cash == 15000.0

    def test_ending_cash(self, ledger_with_flows):
        cf = generate_cash_flow_statement(ledger_with_flows)
        # Cash: 10000 + 5000 - 2000 - 3000 = 10000
        # Bank: 5000
        # Total cash = 15000
        assert cf.ending_cash == 15000.0

    def test_beginning_cash(self, ledger_with_flows):
        cf = generate_cash_flow_statement(ledger_with_flows)
        # beginning = ending - net_change
        assert cf.beginning_cash == 0.0

    def test_empty_ledger(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        cf = generate_cash_flow_statement(ledger)
        assert cf.operating.total == 0.0
        assert cf.investing.total == 0.0
        assert cf.financing.total == 0.0
        assert cf.net_change_in_cash == 0.0

    def test_format_output(self, ledger_with_flows):
        cf = generate_cash_flow_statement(ledger_with_flows)
        text = format_cash_flow_statement(cf)
        assert "CASH FLOW STATEMENT" in text
        assert "OPERATING ACTIVITIES" in text
        assert "INVESTING ACTIVITIES" in text
        assert "FINANCING ACTIVITIES" in text
        assert "NET CHANGE IN CASH" in text
        assert "Ending Cash" in text


class TestIsCashAccount:
    """Test cash account detection."""

    def test_cash_in_code(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        acct = ledger.create_account("cash", "Cash", AccountType.ASSET)
        assert _is_cash_account(acct)

    def test_bank_in_name(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        acct = ledger.create_account("chk", "Checking Account", AccountType.ASSET)
        assert _is_cash_account(acct)

    def test_savings_account(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        acct = ledger.create_account("sav", "Savings", AccountType.ASSET)
        assert _is_cash_account(acct)

    def test_metadata_flag(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        acct = ledger.create_account("mkt", "Money Market", AccountType.ASSET,
                                     metadata={"is_cash": True})
        assert _is_cash_account(acct)

    def test_non_cash_account(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        acct = ledger.create_account("equip", "Equipment", AccountType.ASSET)
        assert not _is_cash_account(acct)

    def test_inventory_not_cash(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)
        acct = ledger.create_account("inv", "Inventory", AccountType.ASSET)
        assert not _is_cash_account(acct)
