"""Tests for v0.6.0 features: SQLite storage, entry search, account tags."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_ledger.models import Account, AccountType, JournalEntry, JournalLine, LedgerData
from agent_ledger.ledger import Ledger
from agent_ledger.storage import Storage, create_storage
from agent_ledger.sqlite_storage import SQLiteStorage


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def json_ledger(tmp_dir):
    """Create a ledger with JSON storage."""
    path = tmp_dir / "test.json"
    storage = Storage(path)
    storage.init(name="Test Ledger", base_currency="USD")
    ledger = Ledger(storage=storage)
    # Add basic accounts
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("revenue", "Revenue", AccountType.REVENUE)
    ledger.create_account("expense", "Expense", AccountType.EXPENSE)
    return ledger


@pytest.fixture
def sqlite_ledger(tmp_dir):
    """Create a ledger with SQLite storage."""
    path = tmp_dir / "test.db"
    storage = SQLiteStorage(path)
    storage.init(name="Test SQLite Ledger", base_currency="USD")
    ledger = Ledger(storage=storage)
    # Add basic accounts
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("revenue", "Revenue", AccountType.REVENUE)
    ledger.create_account("expense", "Expense", AccountType.EXPENSE)
    return ledger


# ── SQLite Storage Tests ──────────────────────────────────────────

class TestSQLiteStorage:
    def test_init_creates_db(self, tmp_dir):
        path = tmp_dir / "new.db"
        storage = SQLiteStorage(path)
        data = storage.init(name="Test", base_currency="EUR")
        assert data.name == "Test"
        assert data.base_currency == "EUR"
        assert path.exists()

    def test_init_already_exists(self, tmp_dir):
        path = tmp_dir / "existing.db"
        storage = SQLiteStorage(path)
        storage.init(name="Test")
        with pytest.raises(FileExistsError):
            storage.init(name="Another")

    def test_load_returns_data(self, tmp_dir):
        path = tmp_dir / "load.db"
        storage = SQLiteStorage(path)
        storage.init(name="My Ledger", base_currency="GBP")
        data = storage.load()
        assert data.name == "My Ledger"
        assert data.base_currency == "GBP"

    def test_load_not_initialized(self, tmp_dir):
        path = tmp_dir / "nope.db"
        storage = SQLiteStorage(path)
        from agent_ledger.exceptions import LedgerNotInitializedError
        with pytest.raises(LedgerNotInitializedError):
            storage.load()

    def test_save_and_load_accounts(self, tmp_dir):
        path = tmp_dir / "accounts.db"
        storage = SQLiteStorage(path)
        storage.init()
        data = storage.load()

        # Add accounts
        data.accounts["cash"] = Account(
            code="cash", name="Cash", account_type=AccountType.ASSET,
            tags=["liquid", "current"],
        )
        data.accounts["rev"] = Account(
            code="rev", name="Revenue", account_type=AccountType.REVENUE,
            tags=["operating"],
        )
        storage.save(data)

        # Reload
        data2 = storage.load()
        assert "cash" in data2.accounts
        assert data2.accounts["cash"].name == "Cash"
        assert data2.accounts["cash"].tags == ["liquid", "current"]
        assert data2.accounts["rev"].tags == ["operating"]

    def test_save_and_load_entries(self, tmp_dir):
        path = tmp_dir / "entries.db"
        storage = SQLiteStorage(path)
        storage.init()
        data = storage.load()

        data.accounts["cash"] = Account(code="cash", name="Cash", account_type=AccountType.ASSET)
        data.accounts["rev"] = Account(code="rev", name="Revenue", account_type=AccountType.REVENUE)

        entry = JournalEntry(
            description="Test entry",
            lines=[
                JournalLine(account_code="cash", debit=100.0, credit=0.0),
                JournalLine(account_code="rev", debit=0.0, credit=100.0),
            ],
            tags=["test", "v060"],
        )
        data.entries.append(entry)
        storage.save(data)

        # Reload
        data2 = storage.load()
        assert len(data2.entries) == 1
        assert data2.entries[0].description == "Test entry"
        assert data2.entries[0].tags == ["test", "v060"]
        assert len(data2.entries[0].lines) == 2

    def test_delete_database(self, tmp_dir):
        path = tmp_dir / "delete.db"
        storage = SQLiteStorage(path)
        storage.init()
        assert path.exists()
        storage.delete()
        assert not path.exists()

    def test_round_trip_full_data(self, tmp_dir):
        """Test that saving and loading preserves all data types."""
        path = tmp_dir / "full.db"
        storage = SQLiteStorage(path)
        storage.init(name="Full Ledger")
        data = storage.load()

        # Add all data types
        data.accounts["cash"] = Account(
            code="cash", name="Cash", account_type=AccountType.ASSET,
            tags=["current"],
        )
        data.accounts["rev"] = Account(
            code="rev", name="Revenue", account_type=AccountType.REVENUE,
        )

        entry = JournalEntry(
            description="Sale",
            lines=[
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="rev", credit=500.0),
            ],
            tags=["sale"],
        )
        data.entries.append(entry)

        # Bank statement
        data.bank_statements.append({
            "id": "stmt-1",
            "account_code": "cash",
            "statement_date": datetime.now(timezone.utc).isoformat(),
            "opening_balance": 0.0,
            "closing_balance": 500.0,
            "currency": "USD",
            "lines": [
                {
                    "id": "line-1",
                    "date": datetime.now(timezone.utc).isoformat(),
                    "description": "Deposit",
                    "amount": 500.0,
                    "reference": "REF-1",
                    "matched_entry_id": None,
                    "status": "unmatched",
                }
            ],
            "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        storage.save(data)

        # Reload and verify
        data2 = storage.load()
        assert len(data2.accounts) == 2
        assert len(data2.entries) == 1
        assert len(data2.bank_statements) == 1
        assert data2.bank_statements[0]["closing_balance"] == 500.0

    def test_search_entries_by_description(self, tmp_dir):
        """Test the fast search method on SQLite storage."""
        path = tmp_dir / "search.db"
        storage = SQLiteStorage(path)
        storage.init()
        data = storage.load()

        data.accounts["cash"] = Account(code="cash", name="Cash", account_type=AccountType.ASSET)
        data.accounts["rev"] = Account(code="rev", name="Revenue", account_type=AccountType.REVENUE)

        for desc in ["Monthly rent payment", "Salary deposit", "Utility bill payment", "Equipment purchase"]:
            data.entries.append(JournalEntry(
                description=desc,
                lines=[
                    JournalLine(account_code="cash", debit=100.0),
                    JournalLine(account_code="rev", credit=100.0),
                ],
            ))

        storage.save(data)

        results = storage.search_entries_by_description("payment")
        assert len(results) == 2  # "Monthly rent payment" and "Utility bill payment"

        results2 = storage.search_entries_by_description("deposit")
        assert len(results2) == 1

        results3 = storage.search_entries_by_description("nonexistent")
        assert len(results3) == 0

    def test_get_account_balance_fast(self, tmp_dir):
        """Test the fast balance computation on SQLite storage."""
        path = tmp_dir / "balance.db"
        storage = SQLiteStorage(path)
        storage.init()
        data = storage.load()

        data.accounts["cash"] = Account(code="cash", name="Cash", account_type=AccountType.ASSET)
        data.accounts["rev"] = Account(code="rev", name="Revenue", account_type=AccountType.REVENUE)

        data.entries.append(JournalEntry(
            description="Sale 1",
            lines=[
                JournalLine(account_code="cash", debit=200.0),
                JournalLine(account_code="rev", credit=200.0),
            ],
        ))
        data.entries.append(JournalEntry(
            description="Sale 2",
            lines=[
                JournalLine(account_code="cash", debit=300.0),
                JournalLine(account_code="rev", credit=300.0),
            ],
        ))

        storage.save(data)

        result = storage.get_account_balance_fast("cash")
        assert result is not None
        assert result["debit_total"] == 500.0
        assert result["credit_total"] == 0.0

    def test_close_connection(self, tmp_dir):
        path = tmp_dir / "close.db"
        storage = SQLiteStorage(path)
        storage.init()
        storage.close()
        assert storage._conn is None


# ── create_storage Factory Tests ──────────────────────────────────

class TestCreateStorage:
    def test_json_extension(self, tmp_dir):
        path = tmp_dir / "ledger.json"
        storage = create_storage(path)
        assert isinstance(storage, Storage)

    def test_db_extension(self, tmp_dir):
        path = tmp_dir / "ledger.db"
        storage = create_storage(path)
        assert isinstance(storage, SQLiteStorage)

    def test_default_is_json(self):
        storage = create_storage()
        assert isinstance(storage, Storage)

    def test_no_extension_is_json(self, tmp_dir):
        path = tmp_dir / "ledger"
        storage = create_storage(path)
        assert isinstance(storage, Storage)


# ── Entry Search Tests ────────────────────────────────────────────

class TestEntrySearch:
    def test_basic_search(self, json_ledger):
        ledger = json_ledger
        ledger.post_entry("Rent payment for office", [
            ("cash", 1000.0, 0.0),
            ("expense", 0.0, 1000.0),
        ])
        ledger.post_entry("Salary payment", [
            ("cash", 2000.0, 0.0),
            ("expense", 0.0, 2000.0),
        ])
        ledger.post_entry("Consulting revenue", [
            ("cash", 0.0, 500.0),
            ("revenue", 500.0, 0.0),
        ])

        results = ledger.search_entries("payment")
        assert len(results) == 2

    def test_case_insensitive_search(self, json_ledger):
        ledger = json_ledger
        ledger.post_entry("MONTHLY RENT", [
            ("cash", 100.0, 0.0),
            ("expense", 0.0, 100.0),
        ])

        results = ledger.search_entries("monthly")
        assert len(results) == 1

        results2 = ledger.search_entries("rent")
        assert len(results2) == 1

    def test_search_with_account_filter(self, json_ledger):
        ledger = json_ledger
        ledger.post_entry("Cash sale", [
            ("cash", 100.0, 0.0),
            ("revenue", 0.0, 100.0),
        ])

        results = ledger.search_entries("sale", account_code="cash")
        assert len(results) == 1

        results2 = ledger.search_entries("sale", account_code="expense")
        assert len(results2) == 0

    def test_search_with_tag_filter(self, json_ledger):
        ledger = json_ledger
        entry = ledger.post_entry("Tagged sale", [
            ("cash", 100.0, 0.0),
            ("revenue", 0.0, 100.0),
        ])
        entry.tags = ["important"]
        ledger.save()

        results = ledger.search_entries("sale", tag="important")
        assert len(results) == 1

        results2 = ledger.search_entries("sale", tag="nonexistent")
        assert len(results2) == 0

    def test_search_with_limit(self, json_ledger):
        ledger = json_ledger
        for i in range(10):
            ledger.post_entry(f"Payment {i}", [
                ("cash", 100.0, 0.0),
                ("expense", 0.0, 100.0),
            ])

        results = ledger.search_entries("Payment", limit=3)
        assert len(results) == 3

    def test_search_no_results(self, json_ledger):
        ledger = json_ledger
        results = ledger.search_entries("nonexistent query")
        assert len(results) == 0


# ── Account Tags Tests ────────────────────────────────────────────

class TestAccountTags:
    def test_account_has_tags_field(self):
        account = Account(
            code="cash", name="Cash", account_type=AccountType.ASSET,
            tags=["liquid", "current"],
        )
        assert account.tags == ["liquid", "current"]

    def test_account_default_tags_empty(self):
        account = Account(code="cash", name="Cash", account_type=AccountType.ASSET)
        assert account.tags == []

    def test_add_tag_to_account(self, json_ledger):
        ledger = json_ledger
        account = ledger.get_account("cash")
        account.tags.append("liquid")
        ledger.save()

        # Reload and verify
        account2 = ledger.get_account("cash")
        assert "liquid" in account2.tags

    def test_remove_tag_from_account(self, json_ledger):
        ledger = json_ledger
        account = ledger.get_account("cash")
        account.tags = ["liquid", "current"]
        ledger.save()

        account.tags.remove("liquid")
        ledger.save()

        account2 = ledger.get_account("cash")
        assert "liquid" not in account2.tags
        assert "current" in account2.tags

    def test_list_accounts_by_tag(self, json_ledger):
        ledger = json_ledger
        cash = ledger.get_account("cash")
        cash.tags = ["liquid"]
        revenue = ledger.get_account("revenue")
        revenue.tags = ["liquid", "operating"]
        ledger.save()

        results = ledger.list_accounts(tag="liquid")
        assert len(results) == 2

        results2 = ledger.list_accounts(tag="operating")
        assert len(results2) == 1
        assert results2[0].code == "revenue"

    def test_list_accounts_tag_no_match(self, json_ledger):
        ledger = json_ledger
        results = ledger.list_accounts(tag="nonexistent")
        assert len(results) == 0

    def test_update_account_with_tags(self, json_ledger):
        ledger = json_ledger
        account = ledger.update_account("cash", tags=["new-tag-1", "new-tag-2"])
        assert account.tags == ["new-tag-1", "new-tag-2"]

        # Verify persistence
        account2 = ledger.get_account("cash")
        assert account2.tags == ["new-tag-1", "new-tag-2"]

    def test_update_account_preserves_other_fields(self, json_ledger):
        ledger = json_ledger
        original_name = ledger.get_account("cash").name
        account = ledger.update_account("cash", tags=["test"])
        assert account.name == original_name
        assert account.tags == ["test"]

    def test_tags_round_trip_json(self, tmp_dir):
        """Tags survive save/load cycle with JSON storage."""
        path = tmp_dir / "tags.json"
        storage = Storage(path)
        storage.init()
        ledger = Ledger(storage=storage)
        ledger.create_account("cash", "Cash", AccountType.ASSET)

        account = ledger.get_account("cash")
        account.tags = ["liquid", "current", "operating"]
        ledger.save()

        # Reload from scratch
        ledger2 = Ledger(storage=Storage(path))
        account2 = ledger2.get_account("cash")
        assert account2.tags == ["liquid", "current", "operating"]

    def test_tags_round_trip_sqlite(self, tmp_dir):
        """Tags survive save/load cycle with SQLite storage."""
        path = tmp_dir / "tags.db"
        storage = SQLiteStorage(path)
        storage.init()
        ledger = Ledger(storage=storage)
        ledger.create_account("cash", "Cash", AccountType.ASSET)

        account = ledger.get_account("cash")
        account.tags = ["liquid", "current"]
        ledger.save()

        # Reload from scratch
        storage2 = SQLiteStorage(path)
        ledger2 = Ledger(storage=storage2)
        account2 = ledger2.get_account("cash")
        assert account2.tags == ["liquid", "current"]


# ── SQLite with Ledger Integration Tests ──────────────────────────

class TestSQLiteLedgerIntegration:
    def test_full_workflow(self, tmp_dir):
        """Test a complete workflow using SQLite storage."""
        path = tmp_dir / "workflow.db"
        storage = SQLiteStorage(path)
        storage.init(name="Integration Test", base_currency="USD")
        ledger = Ledger(storage=storage)

        # Add accounts with tags
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        ledger.create_account("bank", "Bank Account", AccountType.ASSET)
        ledger.create_account("revenue", "Revenue", AccountType.REVENUE)
        ledger.create_account("expense", "Expense", AccountType.EXPENSE)

        # Add tags
        ledger.get_account("cash").tags = ["liquid", "current"]
        ledger.get_account("bank").tags = ["liquid", "current"]
        ledger.get_account("revenue").tags = ["operating"]
        ledger.save()

        # Post entries
        ledger.post_entry("Client payment received", [
            ("bank", 5000.0, 0.0),
            ("revenue", 0.0, 5000.0),
        ])
        ledger.post_entry("Office rent payment", [
            ("bank", 0.0, 1500.0),
            ("expense", 1500.0, 0.0),
        ])

        # Search entries
        results = ledger.search_entries("payment")
        assert len(results) == 2

        # List by tag
        liquid_accounts = ledger.list_accounts(tag="liquid")
        assert len(liquid_accounts) == 2

        # Verify balances
        bank_balance = ledger.get_account_balance("bank")
        assert bank_balance.balance == 3500.0

        # Re-open and verify
        storage2 = SQLiteStorage(path)
        ledger2 = Ledger(storage=storage2)
        bank_balance2 = ledger2.get_account_balance("bank")
        assert bank_balance2.balance == 3500.0

        # Search still works after reload
        results2 = ledger2.search_entries("payment")
        assert len(results2) == 2

    def test_sqlite_handles_many_entries(self, tmp_dir):
        """SQLite should handle hundreds of entries efficiently."""
        path = tmp_dir / "many.db"
        storage = SQLiteStorage(path)
        storage.init()
        ledger = Ledger(storage=storage)

        ledger.create_account("cash", "Cash", AccountType.ASSET)
        ledger.create_account("revenue", "Revenue", AccountType.REVENUE)

        # Post 100 entries
        for i in range(100):
            ledger.post_entry(f"Transaction {i:04d}", [
                ("cash", 10.0, 0.0),
                ("revenue", 0.0, 10.0),
            ])

        balance = ledger.get_account_balance("cash")
        assert balance.balance == 1000.0

        # Search — "Transaction 000" matches 0000-0009 only
        results = ledger.search_entries("Transaction 000")
        assert len(results) == 10  # 0000-0009

    def test_sqlite_account_deletion(self, tmp_dir):
        """Test that removing an account persists correctly."""
        path = tmp_dir / "del.db"
        storage = SQLiteStorage(path)
        storage.init()
        ledger = Ledger(storage=storage)

        ledger.create_account("cash", "Cash", AccountType.ASSET)
        ledger.create_account("temp", "Temporary", AccountType.ASSET)
        ledger.save()

        # Delete temp
        ledger.delete_account("temp")
        ledger.save()

        # Re-open
        storage2 = SQLiteStorage(path)
        ledger2 = Ledger(storage=storage2)
        assert "temp" not in ledger2.data.accounts
        assert "cash" in ledger2.data.accounts
