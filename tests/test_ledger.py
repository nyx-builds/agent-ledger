"""Tests for agent-ledger ledger engine."""

import pytest
from datetime import datetime, timezone, timedelta

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.exceptions import (
    AccountNotFoundError, AccountAlreadyExistsError,
    CannotDeleteAccountError, JournalEntryNotFoundError,
    ReconciliationError,
)


@pytest.fixture
def ledger(tmp_path):
    """Create a fresh ledger for testing."""
    filepath = tmp_path / "ledger.json"
    storage = Storage(filepath)
    storage.init(name="Test Ledger")
    return Ledger(storage)


@pytest.fixture
def ledger_with_accounts(ledger):
    """Create a ledger with basic accounts."""
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("bank", "Bank Account", AccountType.ASSET)
    ledger.create_account("inventory", "Inventory", AccountType.ASSET)
    ledger.create_account("ap", "Accounts Payable", AccountType.LIABILITY)
    ledger.create_account("loan", "Bank Loan", AccountType.LIABILITY)
    ledger.create_account("equity", "Owner's Equity", AccountType.EQUITY)
    ledger.create_account("revenue", "Sales Revenue", AccountType.REVENUE)
    ledger.create_account("expenses", "Operating Expenses", AccountType.EXPENSE)
    return ledger


class TestAccountManagement:
    """Test account CRUD operations."""

    def test_create_account(self, ledger):
        account = ledger.create_account("cash", "Cash", AccountType.ASSET)
        assert account.code == "cash"
        assert account.name == "Cash"
        assert account.account_type == AccountType.ASSET

    def test_create_account_with_options(self, ledger):
        account = ledger.create_account(
            "bank", "Bank Account", AccountType.ASSET,
            currency="EUR", description="Main bank account",
            metadata={"bank": "Deutsche Bank"},
        )
        assert account.currency == "EUR"
        assert account.description == "Main bank account"
        assert account.metadata["bank"] == "Deutsche Bank"

    def test_create_duplicate_account_raises(self, ledger):
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        with pytest.raises(AccountAlreadyExistsError):
            ledger.create_account("cash", "Cash Duplicate", AccountType.ASSET)

    def test_get_account(self, ledger):
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        account = ledger.get_account("cash")
        assert account.name == "Cash"

    def test_get_account_not_found(self, ledger):
        with pytest.raises(AccountNotFoundError):
            ledger.get_account("nonexistent")

    def test_get_account_case_insensitive(self, ledger):
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        account = ledger.get_account("CASH")
        assert account.code == "cash"

    def test_update_account(self, ledger):
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        updated = ledger.update_account("cash", name="Petty Cash", description="Small cash fund")
        assert updated.name == "Petty Cash"
        assert updated.description == "Small cash fund"

    def test_update_account_active(self, ledger):
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        updated = ledger.update_account("cash", active=False)
        assert updated.active is False

    def test_delete_account(self, ledger):
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        ledger.delete_account("cash")
        with pytest.raises(AccountNotFoundError):
            ledger.get_account("cash")

    def test_delete_account_with_balance_raises(self, ledger_with_accounts):
        # Post an entry to give cash a balance
        ledger_with_accounts.post_entry(
            "Initial investment",
            [
                JournalLine(account_code="cash", debit=1000.0),
                JournalLine(account_code="equity", credit=1000.0),
            ],
        )
        with pytest.raises(CannotDeleteAccountError):
            ledger_with_accounts.delete_account("cash")

    def test_list_accounts(self, ledger_with_accounts):
        accounts = ledger_with_accounts.list_accounts()
        assert len(accounts) == 8

    def test_list_accounts_by_type(self, ledger_with_accounts):
        assets = ledger_with_accounts.list_accounts(account_type=AccountType.ASSET)
        assert len(assets) == 3
        assert all(a.account_type == AccountType.ASSET for a in assets)

    def test_list_accounts_sorted(self, ledger_with_accounts):
        accounts = ledger_with_accounts.list_accounts()
        codes = [a.code for a in accounts]
        assert codes == sorted(codes)


class TestJournalEntries:
    """Test journal entry operations."""

    def test_post_entry(self, ledger_with_accounts):
        entry = ledger_with_accounts.post_entry(
            "Sale for cash",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        assert entry.description == "Sale for cash"
        assert entry.total_debits == 500.0
        assert entry.total_credits == 500.0
        assert not entry.reconciled

    def test_post_entry_with_tags(self, ledger_with_accounts):
        entry = ledger_with_accounts.post_entry(
            "Tagged sale",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
            tags=["sale", "cash"],
        )
        assert "sale" in entry.tags
        assert "cash" in entry.tags

    def test_post_entry_invalid_account(self, ledger_with_accounts):
        with pytest.raises(AccountNotFoundError):
            ledger_with_accounts.post_entry(
                "Bad entry",
                [
                    JournalLine(account_code="nonexistent", debit=100.0),
                    JournalLine(account_code="revenue", credit=100.0),
                ],
            )

    def test_get_entry(self, ledger_with_accounts):
        posted = ledger_with_accounts.post_entry(
            "Test",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        retrieved = ledger_with_accounts.get_entry(posted.id)
        assert retrieved.id == posted.id

    def test_get_entry_not_found(self, ledger_with_accounts):
        with pytest.raises(JournalEntryNotFoundError):
            ledger_with_accounts.get_entry("nonexistent-id")

    def test_delete_entry(self, ledger_with_accounts):
        entry = ledger_with_accounts.post_entry(
            "To delete",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        ledger_with_accounts.delete_entry(entry.id)
        with pytest.raises(JournalEntryNotFoundError):
            ledger_with_accounts.get_entry(entry.id)

    def test_delete_reconciled_entry_raises(self, ledger_with_accounts):
        entry = ledger_with_accounts.post_entry(
            "Reconciled",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        ledger_with_accounts.reconcile_entry(entry.id)
        with pytest.raises(ReconciliationError):
            ledger_with_accounts.delete_entry(entry.id)

    def test_list_entries(self, ledger_with_accounts):
        ledger_with_accounts.post_entry(
            "Entry 1",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        ledger_with_accounts.post_entry(
            "Entry 2",
            [
                JournalLine(account_code="bank", debit=200.0),
                JournalLine(account_code="revenue", credit=200.0),
            ],
        )
        entries = ledger_with_accounts.list_entries()
        assert len(entries) == 2

    def test_list_entries_by_account(self, ledger_with_accounts):
        ledger_with_accounts.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        ledger_with_accounts.post_entry(
            "Bank transfer",
            [
                JournalLine(account_code="bank", debit=500.0),
                JournalLine(account_code="cash", credit=500.0),
            ],
        )
        cash_entries = ledger_with_accounts.list_entries(account_code="cash")
        assert len(cash_entries) == 2

    def test_list_entries_by_tag(self, ledger_with_accounts):
        ledger_with_accounts.post_entry(
            "Tagged",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
            tags=["monthly"],
        )
        ledger_with_accounts.post_entry(
            "Untagged",
            [
                JournalLine(account_code="bank", debit=200.0),
                JournalLine(account_code="revenue", credit=200.0),
            ],
        )
        tagged = ledger_with_accounts.list_entries(tag="monthly")
        assert len(tagged) == 1


class TestReconciliation:
    """Test reconciliation operations."""

    def test_reconcile_entry(self, ledger_with_accounts):
        entry = ledger_with_accounts.post_entry(
            "Reconcile me",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        reconciled = ledger_with_accounts.reconcile_entry(entry.id)
        assert reconciled.reconciled is True

    def test_unreconcile_entry(self, ledger_with_accounts):
        entry = ledger_with_accounts.post_entry(
            "Unreconcile me",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        ledger_with_accounts.reconcile_entry(entry.id)
        unreconciled = ledger_with_accounts.unreconcile_entry(entry.id)
        assert unreconciled.reconciled is False

    def test_list_entries_by_reconciliation(self, ledger_with_accounts):
        entry = ledger_with_accounts.post_entry(
            "Reconciled",
            [
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        ledger_with_accounts.reconcile_entry(entry.id)
        reconciled = ledger_with_accounts.list_entries(reconciled=True)
        assert len(reconciled) == 1
        unreconciled = ledger_with_accounts.list_entries(reconciled=False)
        assert len(unreconciled) == 0


class TestBalances:
    """Test balance computation."""

    def test_empty_account_balance(self, ledger_with_accounts):
        balance = ledger_with_accounts.get_account_balance("cash")
        assert balance.balance == 0.0
        assert balance.raw_balance == 0.0

    def test_asset_balance_after_debit(self, ledger_with_accounts):
        ledger_with_accounts.post_entry(
            "Sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        balance = ledger_with_accounts.get_account_balance("cash")
        assert balance.balance == 500.0
        assert balance.debit_total == 500.0
        assert balance.credit_total == 0.0

    def test_revenue_balance_after_credit(self, ledger_with_accounts):
        ledger_with_accounts.post_entry(
            "Sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        balance = ledger_with_accounts.get_account_balance("revenue")
        assert balance.balance == 500.0  # Revenue: credit - debit
        assert balance.raw_balance == -500.0

    def test_multiple_entries_balance(self, ledger_with_accounts):
        ledger_with_accounts.post_entry(
            "Sale 1",
            [
                JournalLine(account_code="cash", debit=300.0),
                JournalLine(account_code="revenue", credit=300.0),
            ],
        )
        ledger_with_accounts.post_entry(
            "Expense",
            [
                JournalLine(account_code="expenses", debit=100.0),
                JournalLine(account_code="cash", credit=100.0),
            ],
        )
        balance = ledger_with_accounts.get_account_balance("cash")
        assert balance.debit_total == 300.0
        assert balance.credit_total == 100.0
        assert balance.balance == 200.0  # 300 - 100

    def test_get_all_balances(self, ledger_with_accounts):
        ledger_with_accounts.post_entry(
            "Sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        balances = ledger_with_accounts.get_all_balances()
        assert len(balances) == 8
        cash_balance = next(b for b in balances if b.account_code == "cash")
        assert cash_balance.balance == 500.0

    def test_account_transactions_with_running_balance(self, ledger_with_accounts):
        ledger_with_accounts.post_entry(
            "Sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        ledger_with_accounts.post_entry(
            "Expense",
            [
                JournalLine(account_code="expenses", debit=200.0),
                JournalLine(account_code="cash", credit=200.0),
            ],
        )
        txns = ledger_with_accounts.get_account_transactions("cash")
        assert len(txns) == 2
        assert txns[0]["debit"] == 500.0
        assert txns[0]["balance"] == 500.0
        assert txns[1]["credit"] == 200.0
        assert txns[1]["balance"] == 300.0  # 500 - 200


class TestComplexScenarios:
    """Test complex accounting scenarios."""

    def test_full_accounting_cycle(self, ledger_with_accounts):
        """Test a complete accounting cycle with multiple transactions."""
        # Initial investment
        ledger_with_accounts.post_entry(
            "Owner investment",
            [
                JournalLine(account_code="cash", debit=10000.0),
                JournalLine(account_code="equity", credit=10000.0),
            ],
        )

        # Buy inventory on credit
        ledger_with_accounts.post_entry(
            "Purchase inventory",
            [
                JournalLine(account_code="inventory", debit=3000.0),
                JournalLine(account_code="ap", credit=3000.0),
            ],
        )

        # Sell inventory for cash
        ledger_with_accounts.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=5000.0),
                JournalLine(account_code="revenue", credit=5000.0),
            ],
        )

        # Pay expenses
        ledger_with_accounts.post_entry(
            "Pay rent",
            [
                JournalLine(account_code="expenses", debit=1000.0),
                JournalLine(account_code="cash", credit=1000.0),
            ],
        )

        # Pay accounts payable
        ledger_with_accounts.post_entry(
            "Pay supplier",
            [
                JournalLine(account_code="ap", debit=2000.0),
                JournalLine(account_code="cash", credit=2000.0),
            ],
        )

        # Check balances
        cash = ledger_with_accounts.get_account_balance("cash")
        assert cash.balance == 12000.0  # 10000 + 5000 - 1000 - 2000

        inventory = ledger_with_accounts.get_account_balance("inventory")
        assert inventory.balance == 3000.0

        ap = ledger_with_accounts.get_account_balance("ap")
        assert ap.balance == 1000.0  # 3000 - 2000

        equity = ledger_with_accounts.get_account_balance("equity")
        assert equity.balance == 10000.0

        revenue = ledger_with_accounts.get_account_balance("revenue")
        assert revenue.balance == 5000.0

        expenses = ledger_with_accounts.get_account_balance("expenses")
        assert expenses.balance == 1000.0

    def test_persistence_roundtrip(self, tmp_path):
        """Test that data persists correctly across ledger instances."""
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()

        # Create and populate
        ledger1 = Ledger(storage)
        ledger1.create_account("cash", "Cash", AccountType.ASSET)
        ledger1.create_account("revenue", "Revenue", AccountType.REVENUE)
        ledger1.post_entry(
            "Sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )

        # Load in a new instance
        storage2 = Storage(filepath)
        ledger2 = Ledger(storage2)
        ledger2.reload()

        cash_balance = ledger2.get_account_balance("cash")
        assert cash_balance.balance == 500.0
