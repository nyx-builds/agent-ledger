"""Tests for journal entry reversal."""

import pytest

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.exceptions import JournalEntryNotFoundError


@pytest.fixture
def ledger_with_entries(tmp_path):
    """Create a ledger with accounts and entries."""
    filepath = tmp_path / "ledger.json"
    storage = Storage(filepath)
    storage.init(name="Reversal Test")
    ledger = Ledger(storage)

    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("revenue", "Sales Revenue", AccountType.REVENUE)
    ledger.create_account("expenses", "Operating Expenses", AccountType.EXPENSE)

    return ledger


class TestEntryReversal:
    """Test journal entry reversal."""

    def test_reverse_entry_creates_new_entry(self, ledger_with_entries):
        entry = ledger_with_entries.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        reversal = ledger_with_entries.reverse_entry(entry.id)

        # Reversal is a new entry
        assert reversal.id != entry.id
        assert "Reversal" in reversal.description

    def test_reverse_entry_swaps_debits_credits(self, ledger_with_entries):
        entry = ledger_with_entries.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        reversal = ledger_with_entries.reverse_entry(entry.id)

        # Original: cash debit 500, revenue credit 500
        # Reversal: cash credit 500, revenue debit 500
        cash_line = next(l for l in reversal.lines if l.account_code == "cash")
        revenue_line = next(l for l in reversal.lines if l.account_code == "revenue")

        assert cash_line.credit == 500.0
        assert cash_line.debit == 0.0
        assert revenue_line.debit == 500.0
        assert revenue_line.credit == 0.0

    def test_reverse_entry_balances_to_zero(self, ledger_with_entries):
        entry = ledger_with_entries.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        ledger_with_entries.reverse_entry(entry.id)

        # After reversal, cash and revenue should net to zero
        cash = ledger_with_entries.get_account_balance("cash")
        revenue = ledger_with_entries.get_account_balance("revenue")
        assert cash.balance == 0.0
        assert revenue.balance == 0.0

    def test_reverse_with_reason(self, ledger_with_entries):
        entry = ledger_with_entries.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        reversal = ledger_with_entries.reverse_entry(entry.id, reason="Posted in error")
        assert "Posted in error" in reversal.description

    def test_reverse_entry_has_reversal_tag(self, ledger_with_entries):
        entry = ledger_with_entries.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        reversal = ledger_with_entries.reverse_entry(entry.id)
        assert "reversal" in reversal.tags

    def test_reverse_entry_metadata(self, ledger_with_entries):
        entry = ledger_with_entries.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        reversal = ledger_with_entries.reverse_entry(entry.id)
        assert reversal.metadata["reversal_of"] == entry.id

    def test_reverse_nonexistent_entry(self, ledger_with_entries):
        with pytest.raises(JournalEntryNotFoundError):
            ledger_with_entries.reverse_entry("nonexistent-id")

    def test_reverse_multiline_entry(self, ledger_with_entries):
        entry = ledger_with_entries.post_entry(
            "Complex entry",
            [
                JournalLine(account_code="cash", debit=300.0),
                JournalLine(account_code="expenses", debit=200.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        reversal = ledger_with_entries.reverse_entry(entry.id)
        assert reversal.total_debits == entry.total_credits
        assert reversal.total_credits == entry.total_debits

    def test_double_reversal_restores_balance(self, ledger_with_entries):
        entry = ledger_with_entries.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=1000.0),
                JournalLine(account_code="revenue", credit=1000.0),
            ],
        )
        reversal = ledger_with_entries.reverse_entry(entry.id)
        reverse_reversal = ledger_with_entries.reverse_entry(reversal.id)

        # After double reversal, balances should be restored
        cash = ledger_with_entries.get_account_balance("cash")
        assert cash.balance == 1000.0

    def test_reverse_creates_audit_entry(self, ledger_with_entries):
        entry = ledger_with_entries.post_entry(
            "Cash sale",
            [
                JournalLine(account_code="cash", debit=500.0),
                JournalLine(account_code="revenue", credit=500.0),
            ],
        )
        ledger_with_entries.reverse_entry(entry.id)

        from agent_ledger.audit import AuditAction
        audit_entries = ledger_with_entries.audit.list_entries(
            action=AuditAction.ENTRY_REVERSE
        )
        assert len(audit_entries) == 1
