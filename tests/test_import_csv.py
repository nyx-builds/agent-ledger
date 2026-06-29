"""Tests for CSV import functionality."""

import pytest

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.import_csv import import_accounts_csv, import_entries_csv


@pytest.fixture
def empty_ledger(tmp_path):
    """Create an empty ledger."""
    filepath = tmp_path / "ledger.json"
    storage = Storage(filepath)
    storage.init(name="Import Test")
    return Ledger(storage)


@pytest.fixture
def ledger_with_accounts(tmp_path):
    """Create a ledger with accounts for entry import."""
    filepath = tmp_path / "ledger.json"
    storage = Storage(filepath)
    storage.init(name="Import Test")
    ledger = Ledger(storage)
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("revenue", "Revenue", AccountType.REVENUE)
    ledger.create_account("expenses", "Expenses", AccountType.EXPENSE)
    return ledger


class TestImportAccounts:
    """Test CSV account import."""

    def test_import_basic_accounts(self, empty_ledger):
        csv_data = "code,name,type\ncash,Cash,asset\nap,Accounts Payable,liability\n"
        result = import_accounts_csv(empty_ledger, csv_data)
        assert result.imported == 2
        assert result.success

    def test_import_with_all_fields(self, empty_ledger):
        csv_data = (
            "code,name,type,currency,description,parent_code\n"
            "cash,Cash,asset,USD,Main cash,\n"
            "bank,Bank,asset,USD,Primary bank,\n"
        )
        result = import_accounts_csv(empty_ledger, csv_data)
        assert result.imported == 2

    def test_import_default_type(self, empty_ledger):
        csv_data = "code,name\nmisc,Misc Account\n"
        result = import_accounts_csv(empty_ledger, csv_data)
        assert result.imported == 1
        account = empty_ledger.get_account("misc")
        assert account.account_type == AccountType.ASSET  # default

    def test_import_duplicate_skipped(self, empty_ledger):
        empty_ledger.create_account("cash", "Cash", AccountType.ASSET)
        csv_data = "code,name,type\ncash,Cash Duplicate,asset\n"
        result = import_accounts_csv(empty_ledger, csv_data, skip_errors=True)
        assert result.skipped == 1
        assert result.imported == 0

    def test_import_duplicate_fails_without_skip(self, empty_ledger):
        empty_ledger.create_account("cash", "Cash", AccountType.ASSET)
        csv_data = "code,name,type\ncash,Cash Duplicate,asset\n"
        result = import_accounts_csv(empty_ledger, csv_data, skip_errors=False)
        assert not result.success
        assert len(result.errors) > 0

    def test_import_invalid_type(self, empty_ledger):
        csv_data = "code,name,type\ncash,Cash,invalid_type\n"
        result = import_accounts_csv(empty_ledger, csv_data, skip_errors=True)
        assert result.imported == 0
        assert len(result.errors) > 0

    def test_import_missing_required_fields(self, empty_ledger):
        csv_data = "code,name,type\n,Cash,asset\n"
        result = import_accounts_csv(empty_ledger, csv_data, skip_errors=True)
        assert result.imported == 0
        assert len(result.errors) > 0

    def test_import_uses_base_currency_default(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init(name="EUR Ledger", base_currency="EUR")
        ledger = Ledger(storage)
        csv_data = "code,name,type\nbank,Bank,asset\n"
        result = import_accounts_csv(ledger, csv_data)
        assert result.imported == 1
        account = ledger.get_account("bank")
        assert account.currency == "EUR"

    def test_import_with_parent_code(self, empty_ledger):
        empty_ledger.create_account("assets", "Assets", AccountType.ASSET)
        csv_data = "code,name,type,parent_code\ncash,Cash,asset,assets\n"
        result = import_accounts_csv(empty_ledger, csv_data)
        assert result.imported == 1
        account = empty_ledger.get_account("cash")
        assert account.parent_code == "assets"


class TestImportEntries:
    """Test CSV journal entry import."""

    def test_import_simple_entry(self, ledger_with_accounts):
        csv_data = (
            "entry_description,account_code,debit,credit\n"
            "Cash sale,cash,500,0\n"
            "Cash sale,revenue,0,500\n"
        )
        result = import_entries_csv(ledger_with_accounts, csv_data)
        assert result.imported == 1

    def test_import_multiple_entries(self, ledger_with_accounts):
        csv_data = (
            "entry_description,account_code,debit,credit\n"
            "Sale,cash,500,0\n"
            "Sale,revenue,0,500\n"
            "Expense,expenses,200,0\n"
            "Expense,cash,0,200\n"
        )
        result = import_entries_csv(ledger_with_accounts, csv_data)
        assert result.imported == 2

    def test_import_with_line_description(self, ledger_with_accounts):
        csv_data = (
            "entry_description,account_code,debit,credit,line_description\n"
            "Sale,cash,500,0,Received cash\n"
            "Sale,revenue,0,500,Earned revenue\n"
        )
        result = import_entries_csv(ledger_with_accounts, csv_data)
        assert result.imported == 1

    def test_import_default_debit_credit(self, ledger_with_accounts):
        csv_data = (
            "entry_description,account_code,debit,credit\n"
            "Sale,cash,500,\n"
            "Sale,revenue,,500\n"
        )
        result = import_entries_csv(ledger_with_accounts, csv_data)
        assert result.imported == 1

    def test_import_unbalanced_entry_fails(self, ledger_with_accounts):
        csv_data = (
            "entry_description,account_code,debit,credit\n"
            "Bad entry,cash,500,0\n"
            "Bad entry,revenue,0,400\n"
        )
        result = import_entries_csv(ledger_with_accounts, csv_data, skip_errors=True)
        assert result.skipped == 1

    def test_import_invalid_account(self, ledger_with_accounts):
        csv_data = (
            "entry_description,account_code,debit,credit\n"
            "Bad,nonexistent,500,0\n"
            "Bad,cash,0,500\n"
        )
        result = import_entries_csv(ledger_with_accounts, csv_data, skip_errors=True)
        assert result.skipped == 1

    def test_import_missing_required_fields(self, ledger_with_accounts):
        csv_data = "entry_description,account_code,debit,credit\n,cash,500,0\n"
        result = import_entries_csv(ledger_with_accounts, csv_data, skip_errors=True)
        assert result.imported == 0
        assert len(result.errors) > 0

    def test_import_entries_check_balances(self, ledger_with_accounts):
        csv_data = (
            "entry_description,account_code,debit,credit\n"
            "Sale,cash,1000,0\n"
            "Sale,revenue,0,1000\n"
        )
        result = import_entries_csv(ledger_with_accounts, csv_data)
        assert result.imported == 1

        cash = ledger_with_accounts.get_account_balance("cash")
        revenue = ledger_with_accounts.get_account_balance("revenue")
        assert cash.balance == 1000.0
        assert revenue.balance == 1000.0
