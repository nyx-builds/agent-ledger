"""Tests for CSV export functionality."""

import pytest
import csv
import io
from pathlib import Path
import tempfile

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.export import (
    export_accounts_csv, export_entries_csv, export_trial_balance_csv,
    export_income_statement_csv, export_balance_sheet_csv,
    export_account_transactions_csv, export_hierarchy_csv,
    write_csv_to_file,
)


@pytest.fixture
def ledger_with_data():
    """Create a ledger with accounts and entries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "test_ledger.json"
        storage = Storage(filepath)
        ledger = Ledger(storage)
        ledger._data = storage.init(name="Test Ledger")

        # Create accounts
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        ledger.create_account("bank", "Bank Account", AccountType.ASSET, parent_code="cash")
        ledger.create_account("revenue", "Sales Revenue", AccountType.REVENUE)
        ledger.create_account("expenses", "Office Expenses", AccountType.EXPENSE)
        ledger.create_account("equity", "Owner Equity", AccountType.EQUITY)
        ledger.create_account("payable", "Accounts Payable", AccountType.LIABILITY)

        # Post entries
        ledger.post_entry(
            "Cash sale",
            lines=[
                JournalLine(account_code="cash", debit=1000.0, credit=0.0),
                JournalLine(account_code="revenue", debit=0.0, credit=1000.0),
            ],
            tags=["sale"],
        )
        ledger.post_entry(
            "Office supplies",
            lines=[
                JournalLine(account_code="expenses", debit=200.0, credit=0.0),
                JournalLine(account_code="cash", debit=0.0, credit=200.0),
            ],
            tags=["expense"],
        )

        yield ledger


def parse_csv(csv_string: str) -> list[dict]:
    """Parse CSV string to list of dicts."""
    reader = csv.DictReader(io.StringIO(csv_string))
    return list(reader)


class TestExportAccountsCSV:
    def test_export_accounts_with_balances(self, ledger_with_data):
        csv_content = export_accounts_csv(ledger_with_data)
        rows = parse_csv(csv_content)
        assert len(rows) == 6  # 6 accounts

        # Check cash account
        cash_row = [r for r in rows if r["code"] == "cash"][0]
        assert cash_row["name"] == "Cash"
        assert cash_row["type"] == "asset"
        assert float(cash_row["balance"]) == 800.0  # 1000 - 200

    def test_export_accounts_without_balances(self, ledger_with_data):
        csv_content = export_accounts_csv(ledger_with_data, include_balances=False)
        rows = parse_csv(csv_content)
        assert "balance" not in rows[0]
        assert "code" in rows[0]

    def test_export_accounts_has_parent_column(self, ledger_with_data):
        csv_content = export_accounts_csv(ledger_with_data)
        rows = parse_csv(csv_content)
        bank_row = [r for r in rows if r["code"] == "bank"][0]
        assert bank_row["parent_code"] == "cash"


class TestExportEntriesCSV:
    def test_export_entries(self, ledger_with_data):
        csv_content = export_entries_csv(ledger_with_data)
        rows = parse_csv(csv_content)
        # 2 entries × 2 lines each = 4 rows
        assert len(rows) == 4

    def test_export_entries_filter_by_account(self, ledger_with_data):
        csv_content = export_entries_csv(ledger_with_data, account_code="cash")
        rows = parse_csv(csv_content)
        # When filtering by account, we get the full entries that contain cash lines
        # Entry 1 (Cash sale) has cash + revenue = 2 lines
        # Entry 2 (Office supplies) has expenses + cash = 2 lines
        # Total: 4 lines
        assert len(rows) == 4
        # But both entries have at least one cash line
        entry_ids = set(r["entry_id"] for r in rows)
        assert len(entry_ids) == 2

    def test_export_entries_filter_by_tag(self, ledger_with_data):
        csv_content = export_entries_csv(ledger_with_data, tag="sale")
        rows = parse_csv(csv_content)
        assert len(rows) == 2

    def test_export_entries_has_all_columns(self, ledger_with_data):
        csv_content = export_entries_csv(ledger_with_data)
        reader = csv.reader(io.StringIO(csv_content))
        header = next(reader)
        assert "entry_id" in header
        assert "line_debit" in header
        assert "line_credit" in header


class TestExportTrialBalanceCSV:
    def test_export_trial_balance(self, ledger_with_data):
        csv_content = export_trial_balance_csv(ledger_with_data)
        rows = parse_csv(csv_content)
        # Should have account rows + total row
        assert len(rows) >= 3

        # Check total row
        total_row = rows[-1]
        assert total_row["account_name"] == "TOTAL"


class TestExportIncomeStatementCSV:
    def test_export_income_statement(self, ledger_with_data):
        csv_content = export_income_statement_csv(ledger_with_data)
        rows = parse_csv(csv_content)

        # Find revenue and expense sections
        revenue_rows = [r for r in rows if r["section"] == "revenue"]
        expense_rows = [r for r in rows if r["section"] == "expense"]
        net_income_row = [r for r in rows if r["section"] == "net_income"]

        assert len(revenue_rows) >= 1
        assert len(expense_rows) >= 1
        assert len(net_income_row) == 1
        assert float(net_income_row[0]["amount"]) == 800.0  # 1000 - 200


class TestExportBalanceSheetCSV:
    def test_export_balance_sheet(self, ledger_with_data):
        csv_content = export_balance_sheet_csv(ledger_with_data)
        rows = parse_csv(csv_content)

        # Should have assets, liabilities, equity sections
        sections = set(r["section"] for r in rows if r["section"])
        assert "asset" in sections
        assert "equity" in sections


class TestExportHierarchyCSV:
    def test_export_hierarchy(self, ledger_with_data):
        csv_content = export_hierarchy_csv(ledger_with_data)
        rows = parse_csv(csv_content)

        assert len(rows) == 6  # 6 accounts

        # Check bank has parent
        bank_row = [r for r in rows if r["code"] == "bank"][0]
        assert bank_row["parent_code"] == "cash"
        assert int(bank_row["depth"]) == 1

        # Check cash has correct is_leaf
        cash_row = [r for r in rows if r["code"] == "cash"][0]
        assert cash_row["is_leaf"] == "False"


class TestExportAccountTransactionsCSV:
    def test_export_account_transactions(self, ledger_with_data):
        csv_content = export_account_transactions_csv(ledger_with_data, "cash")
        rows = parse_csv(csv_content)

        # Cash has 2 transactions
        assert len(rows) == 2

        # Check running balance
        first_tx = rows[0]
        assert float(first_tx["debit"]) == 1000.0
        assert float(first_tx["balance"]) == 1000.0


class TestWriteCSVToFile:
    def test_write_csv_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "subdir" / "output.csv"
            write_csv_to_file("a,b,c\n1,2,3\n", str(filepath))
            assert filepath.exists()
            content = filepath.read_text()
            assert "a,b,c" in content
