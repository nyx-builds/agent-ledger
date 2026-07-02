"""Tests for bank reconciliation module."""

import pytest
from datetime import datetime, timezone
from pathlib import Path

from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.models import AccountType, JournalLine
from agent_ledger.reconciliation import BankReconciliation
from agent_ledger.exceptions import (
    BankStatementNotFoundError,
    ReconciliationItemNotFoundError,
    InvalidReconciliationStateError,
    AccountNotFoundError,
)


@pytest.fixture
def ledger(tmp_path):
    """Create a fresh ledger for testing."""
    filepath = tmp_path / "test_ledger.json"
    storage = Storage(filepath)
    data = storage.init(name="Test Ledger")
    ledger = Ledger(storage)
    ledger._data = data
    return ledger


@pytest.fixture
def ledger_with_accounts(ledger):
    """Create a ledger with cash and revenue accounts."""
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("bank", "Bank Account", AccountType.ASSET)
    ledger.create_account("revenue", "Revenue", AccountType.REVENUE)
    ledger.create_account("expenses", "Expenses", AccountType.EXPENSE)
    return ledger


@pytest.fixture
def ledger_with_entries(ledger_with_accounts):
    """Create a ledger with some journal entries."""
    l = ledger_with_accounts
    
    # Entry 1: Revenue deposit
    l.post_entry(
        "Customer payment",
        [
            JournalLine(account_code="cash", debit=1000.0, credit=0.0),
            JournalLine(account_code="revenue", debit=0.0, credit=1000.0),
        ],
    )
    
    # Entry 2: Expense payment
    l.post_entry(
        "Office supplies",
        [
            JournalLine(account_code="expenses", debit=200.0, credit=0.0),
            JournalLine(account_code="cash", debit=0.0, credit=200.0),
        ],
    )
    
    # Entry 3: Another revenue
    l.post_entry(
        "Consulting fee",
        [
            JournalLine(account_code="bank", debit=500.0, credit=0.0),
            JournalLine(account_code="revenue", debit=0.0, credit=500.0),
        ],
    )
    
    return l


class TestBankStatementCreation:
    def test_create_statement(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        stmt = recon.create_statement(
            account_code="cash",
            closing_balance=800.0,
            opening_balance=0.0,
        )
        assert stmt.id
        assert stmt.account_code == "cash"
        assert stmt.closing_balance == 800.0
        assert stmt.opening_balance == 0.0
        assert stmt.status == "open"
        assert len(stmt.lines) == 0

    def test_create_statement_with_date(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        date = datetime(2024, 6, 30, tzinfo=timezone.utc)
        stmt = recon.create_statement(
            account_code="cash",
            statement_date=date,
            closing_balance=1000.0,
        )
        assert stmt.statement_date == date

    def test_create_statement_invalid_account(self, ledger):
        recon = BankReconciliation(ledger)
        with pytest.raises(AccountNotFoundError):
            recon.create_statement(
                account_code="nonexistent",
                closing_balance=0.0,
            )

    def test_create_multiple_statements(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        s1 = recon.create_statement("cash", closing_balance=100.0)
        s2 = recon.create_statement("bank", closing_balance=500.0)
        statements = recon.list_statements()
        assert len(statements) == 2


class TestStatementLines:
    def test_add_statement_line(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(
            statement_id=stmt.id,
            description="Customer payment",
            amount=1000.0,
            reference="CHK001",
        )
        assert line.id
        assert line.description == "Customer payment"
        assert line.amount == 1000.0
        assert line.reference == "CHK001"
        assert line.status == "unmatched"

    def test_add_negative_amount_line(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(
            statement_id=stmt.id,
            description="ATM withdrawal",
            amount=-200.0,
        )
        assert line.amount == -200.0

    def test_add_statement_lines_batch(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        lines = [
            {"description": "Deposit", "amount": 1000.0, "reference": "D1"},
            {"description": "Withdrawal", "amount": -200.0, "reference": "W1"},
        ]
        created = recon.add_statement_lines_batch(stmt.id, lines)
        assert len(created) == 2
        assert created[0].amount == 1000.0
        assert created[1].amount == -200.0

    def test_add_line_to_nonexistent_statement(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        with pytest.raises(BankStatementNotFoundError):
            recon.add_statement_line(
                statement_id="nonexistent",
                amount=100.0,
            )


class TestManualMatching:
    def test_match_entry(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(stmt.id, description="Customer payment", amount=1000.0)
        
        entries = ledger_with_entries.list_entries(account_code="cash")
        # Find the entry with cash debit of 1000
        cash_debit_entry = None
        for e in entries:
            for l in e.lines:
                if l.account_code == "cash" and l.debit == 1000.0:
                    cash_debit_entry = e
                    break
        
        assert cash_debit_entry is not None
        
        result = recon.match_entry(stmt.id, line.id, cash_debit_entry.id)
        assert result.status == "matched"
        assert result.matched_entry_id == cash_debit_entry.id

    def test_match_wrong_account_entry(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(stmt.id, description="Payment", amount=1000.0)
        
        # Try to match an entry that's not for the cash account
        entries = ledger_with_entries.list_entries(account_code="bank")
        bank_entry = entries[0] if entries else None
        
        if bank_entry:
            with pytest.raises(InvalidReconciliationStateError):
                recon.match_entry(stmt.id, line.id, bank_entry.id)

    def test_unmatch_entry(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(stmt.id, description="Payment", amount=1000.0)
        
        entries = ledger_with_entries.list_entries(account_code="cash")
        cash_entry = None
        for e in entries:
            for l in e.lines:
                if l.account_code == "cash" and l.debit == 1000.0:
                    cash_entry = e
                    break
        
        recon.match_entry(stmt.id, line.id, cash_entry.id)
        result = recon.unmatch_entry(stmt.id, line.id)
        assert result.status == "unmatched"
        assert result.matched_entry_id is None

    def test_unmatch_unmatched_line(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(stmt.id, description="Payment", amount=100.0)
        
        with pytest.raises(InvalidReconciliationStateError):
            recon.unmatch_entry(stmt.id, line.id)

    def test_match_already_matched_line(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(stmt.id, description="Payment", amount=1000.0)
        
        entries = ledger_with_entries.list_entries(account_code="cash")
        cash_entry = None
        for e in entries:
            for l in e.lines:
                if l.account_code == "cash" and l.debit == 1000.0:
                    cash_entry = e
                    break
        
        recon.match_entry(stmt.id, line.id, cash_entry.id)
        
        with pytest.raises(InvalidReconciliationStateError):
            recon.match_entry(stmt.id, line.id, cash_entry.id)

    def test_match_nonexistent_line(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        
        with pytest.raises(ReconciliationItemNotFoundError):
            recon.match_entry(stmt.id, "nonexistent-line", "some-entry")


class TestAutoMatching:
    def test_auto_match_by_amount(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        
        # Add lines that should match the cash entries
        recon.add_statement_line(stmt.id, description="Customer payment", amount=1000.0)
        recon.add_statement_line(stmt.id, description="Office supplies", amount=-200.0)
        
        result = recon.auto_match(stmt.id)
        assert result["matched"] >= 1  # At least the 1000 deposit should match

    def test_auto_match_with_tolerance(self, ledger_with_accounts):
        # Create a specific entry with known amount
        ledger_with_accounts.post_entry(
            "Exact deposit",
            [
                JournalLine(account_code="cash", debit=500.0, credit=0.0),
                JournalLine(account_code="revenue", debit=0.0, credit=500.0),
            ],
        )
        
        recon = BankReconciliation(ledger_with_accounts)
        stmt = recon.create_statement("cash", closing_balance=500.0)
        recon.add_statement_line(stmt.id, description="Close deposit", amount=499.5)
        
        result = recon.auto_match(stmt.id, tolerance=0.50)
        assert result["matched"] == 1


class TestDispute:
    def test_mark_disputed(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(stmt.id, description="Suspicious", amount=999.0)
        
        result = recon.mark_disputed(stmt.id, line.id, reason="Unknown transaction")
        assert result.status == "disputed"

    def test_mark_disputed_nonexistent_line(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        
        with pytest.raises(ReconciliationItemNotFoundError):
            recon.mark_disputed(stmt.id, "nonexistent", reason="Test")


class TestReconciliation:
    def test_reconcile_status(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        recon.add_statement_line(stmt.id, description="Deposit", amount=1000.0)
        
        result = recon.reconcile(stmt.id)
        assert result.statement_id == stmt.id
        assert result.total_statement_lines == 1
        assert result.matched == 0
        assert result.unmatched_statement == 1
        assert result.statement_closing_balance == 800.0

    def test_reconcile_after_matching(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(stmt.id, description="Customer payment", amount=1000.0)
        
        entries = ledger_with_entries.list_entries(account_code="cash")
        cash_entry = None
        for e in entries:
            for l in e.lines:
                if l.account_code == "cash" and l.debit == 1000.0:
                    cash_entry = e
                    break
        
        recon.match_entry(stmt.id, line.id, cash_entry.id)
        result = recon.reconcile(stmt.id)
        assert result.matched == 1
        assert result.unmatched_statement == 0


class TestCompleteReconciliation:
    def test_complete_reconciliation(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        line = recon.add_statement_line(stmt.id, description="Customer payment", amount=1000.0)
        
        entries = ledger_with_entries.list_entries(account_code="cash")
        cash_entry = None
        for e in entries:
            for l in e.lines:
                if l.account_code == "cash" and l.debit == 1000.0:
                    cash_entry = e
                    break
        
        recon.match_entry(stmt.id, line.id, cash_entry.id)
        result = recon.complete_reconciliation(stmt.id)
        assert result.matched == 1
        assert stmt.status == "completed"
        
        # Check that the entry was reconciled in the ledger
        entry = ledger_with_entries.get_entry(cash_entry.id)
        assert entry.reconciled is True

    def test_complete_with_unmatched_lines(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        stmt = recon.create_statement("cash", closing_balance=800.0)
        recon.add_statement_line(stmt.id, description="Unknown", amount=999.0)
        
        with pytest.raises(InvalidReconciliationStateError):
            recon.complete_reconciliation(stmt.id)


class TestListStatements:
    def test_list_all_statements(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        recon.create_statement("cash", closing_balance=100.0)
        recon.create_statement("bank", closing_balance=500.0)
        
        statements = recon.list_statements()
        assert len(statements) == 2

    def test_list_statements_by_account(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        recon.create_statement("cash", closing_balance=100.0)
        recon.create_statement("bank", closing_balance=500.0)
        
        statements = recon.list_statements(account_code="cash")
        assert len(statements) == 1
        assert statements[0].account_code == "cash"

    def test_list_statements_by_status(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        recon.create_statement("cash", closing_balance=100.0)
        
        statements = recon.list_statements(status="open")
        assert len(statements) == 1
        
        statements = recon.list_statements(status="completed")
        assert len(statements) == 0


class TestDeleteStatement:
    def test_delete_statement(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        stmt = recon.create_statement("cash", closing_balance=100.0)
        
        recon.delete_statement(stmt.id)
        statements = recon.list_statements()
        assert len(statements) == 0

    def test_delete_nonexistent_statement(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        with pytest.raises(BankStatementNotFoundError):
            recon.delete_statement("nonexistent")


class TestUnreconciledEntries:
    def test_get_unreconciled_entries(self, ledger_with_entries):
        recon = BankReconciliation(ledger_with_entries)
        entries = recon.get_unreconciled_entries("cash")
        assert len(entries) >= 1  # At least one cash entry that's unreconciled

    def test_get_unreconciled_entries_empty(self, ledger_with_accounts):
        recon = BankReconciliation(ledger_with_accounts)
        entries = recon.get_unreconciled_entries("cash")
        assert len(entries) == 0


class TestPersistence:
    def test_statements_persist_across_reload(self, ledger_with_accounts, tmp_path):
        recon = BankReconciliation(ledger_with_accounts)
        stmt = recon.create_statement("cash", closing_balance=100.0)
        line = recon.add_statement_line(stmt.id, description="Test", amount=50.0)
        
        # Reload ledger
        storage = Storage(tmp_path / "test_ledger.json")
        ledger2 = Ledger(storage)
        ledger2.reload()
        
        recon2 = BankReconciliation(ledger2)
        statements = recon2.list_statements()
        assert len(statements) == 1
        assert statements[0].account_code == "cash"
        assert len(statements[0].lines) == 1
        assert statements[0].lines[0].description == "Test"
