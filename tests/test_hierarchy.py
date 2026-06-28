"""Tests for account hierarchy and rollup functionality."""

import pytest
from pathlib import Path
import tempfile

from agent_ledger.models import AccountType
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.hierarchy import AccountHierarchy
from agent_ledger.exceptions import AccountNotFoundError, AccountHasChildrenError


@pytest.fixture
def ledger_with_hierarchy():
    """Create a ledger with a hierarchical account structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "test_ledger.json"
        storage = Storage(filepath)
        ledger = Ledger(storage)
        ledger._data = storage.init()

        # Root accounts
        ledger.create_account("assets", "Assets", AccountType.ASSET)
        ledger.create_account("liabilities", "Liabilities", AccountType.LIABILITY)
        ledger.create_account("equity", "Equity", AccountType.EQUITY)
        ledger.create_account("revenue", "Revenue", AccountType.REVENUE)
        ledger.create_account("expenses", "Expenses", AccountType.EXPENSE)

        # Sub-accounts
        ledger.create_account("current_assets", "Current Assets", AccountType.ASSET, parent_code="assets")
        ledger.create_account("fixed_assets", "Fixed Assets", AccountType.ASSET, parent_code="assets")
        ledger.create_account("cash", "Cash", AccountType.ASSET, parent_code="current_assets")
        ledger.create_account("bank", "Bank Account", AccountType.ASSET, parent_code="current_assets")
        ledger.create_account("equipment", "Equipment", AccountType.ASSET, parent_code="fixed_assets")

        yield ledger


class TestAccountHierarchy:
    def test_get_children(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        children = h.get_children("assets")
        codes = [c.code for c in children]
        assert "current_assets" in codes
        assert "fixed_assets" in codes
        assert len(children) == 2

    def test_get_children_leaf(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        children = h.get_children("cash")
        assert children == []

    def test_get_all_descendants(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        descendants = h.get_all_descendants("assets")
        codes = [d.code for d in descendants]
        assert "current_assets" in codes
        assert "fixed_assets" in codes
        assert "cash" in codes
        assert "bank" in codes
        assert "equipment" in codes
        assert len(descendants) == 5

    def test_get_parent(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        parent = h.get_parent("cash")
        assert parent.code == "current_assets"

    def test_get_parent_root(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        parent = h.get_parent("assets")
        assert parent is None

    def test_get_ancestors(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        ancestors = h.get_ancestors("cash")
        codes = [a.code for a in ancestors]
        assert codes == ["current_assets", "assets"]

    def test_get_root(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        root = h.get_root("cash")
        assert root.code == "assets"

    def test_get_root_for_root_account(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        root = h.get_root("assets")
        assert root.code == "assets"

    def test_is_leaf(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        assert h.is_leaf("cash") is True
        assert h.is_leaf("assets") is False
        assert h.is_leaf("current_assets") is False

    def test_is_root(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        assert h.is_root("assets") is True
        assert h.is_root("cash") is False

    def test_get_depth(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        assert h.get_depth("assets") == 0
        assert h.get_depth("current_assets") == 1
        assert h.get_depth("cash") == 2

    def test_get_tree(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        tree = h.get_tree("assets")
        assert len(tree) == 1
        assert tree[0]["account"].code == "assets"
        assert len(tree[0]["children"]) == 2  # current_assets, fixed_assets

    def test_get_tree_all_roots(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        tree = h.get_tree()
        root_codes = [t["account"].code for t in tree]
        assert "assets" in root_codes
        assert "liabilities" in root_codes
        assert "equity" in root_codes
        assert "revenue" in root_codes
        assert "expenses" in root_codes

    def test_format_tree(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        tree_str = h.format_tree("assets")
        assert "assets" in tree_str
        assert "current_assets" in tree_str
        assert "cash" in tree_str
        assert "equipment" in tree_str

    def test_validate_hierarchy_no_issues(self, ledger_with_hierarchy):
        h = AccountHierarchy(ledger_with_hierarchy)
        warnings = h.validate_hierarchy()
        assert len(warnings) == 0

    def test_validate_hierarchy_missing_parent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test_ledger.json"
            storage = Storage(filepath)
            ledger = Ledger(storage)
            ledger._data = storage.init()
            ledger.create_account("cash", "Cash", AccountType.ASSET)
            # Manually set a bad parent
            ledger.data.accounts["cash"].parent_code = "nonexistent"
            h = AccountHierarchy(ledger)
            warnings = h.validate_hierarchy()
            assert any("non-existent parent" in w for w in warnings)

    def test_validate_hierarchy_type_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test_ledger.json"
            storage = Storage(filepath)
            ledger = Ledger(storage)
            ledger._data = storage.init()
            ledger.create_account("assets", "Assets", AccountType.ASSET)
            # Create liability account but set parent to asset
            account = ledger.create_account("loan", "Loan", AccountType.LIABILITY)
            ledger.data.accounts["loan"].parent_code = "assets"
            h = AccountHierarchy(ledger)
            warnings = h.validate_hierarchy()
            assert any("different type" in w for w in warnings)


class TestRollupBalance:
    def test_rollup_single_account(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test_ledger.json"
            storage = Storage(filepath)
            ledger = Ledger(storage)
            ledger._data = storage.init()

            ledger.create_account("cash", "Cash", AccountType.ASSET)
            ledger.create_account("equity", "Equity", AccountType.EQUITY)

            from agent_ledger.models import JournalLine
            ledger.post_entry("Init", lines=[
                JournalLine(account_code="cash", debit=500.0, credit=0.0),
                JournalLine(account_code="equity", debit=0.0, credit=500.0),
            ])

            h = AccountHierarchy(ledger)
            rollup = h.get_rollup_balance("cash")
            assert rollup.balance == 500.0

    def test_rollup_with_children(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test_ledger.json"
            storage = Storage(filepath)
            ledger = Ledger(storage)
            ledger._data = storage.init()

            ledger.create_account("assets", "Assets", AccountType.ASSET)
            ledger.create_account("cash", "Cash", AccountType.ASSET, parent_code="assets")
            ledger.create_account("bank", "Bank", AccountType.ASSET, parent_code="assets")
            ledger.create_account("equity", "Equity", AccountType.EQUITY)

            from agent_ledger.models import JournalLine
            ledger.post_entry("Cash in", lines=[
                JournalLine(account_code="cash", debit=300.0, credit=0.0),
                JournalLine(account_code="equity", debit=0.0, credit=300.0),
            ])
            ledger.post_entry("Bank in", lines=[
                JournalLine(account_code="bank", debit=700.0, credit=0.0),
                JournalLine(account_code="equity", debit=0.0, credit=700.0),
            ])

            h = AccountHierarchy(ledger)

            # Rollup for "assets" should include cash + bank
            rollup = h.get_rollup_balance("assets")
            assert rollup.balance == 1000.0  # 300 + 700

            # Own balance of "assets" should be 0
            own = ledger.get_account_balance("assets")
            assert own.balance == 0.0

    def test_rollup_multi_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test_ledger.json"
            storage = Storage(filepath)
            ledger = Ledger(storage)
            ledger._data = storage.init()

            ledger.create_account("assets", "Assets", AccountType.ASSET)
            ledger.create_account("current", "Current", AccountType.ASSET, parent_code="assets")
            ledger.create_account("cash", "Cash", AccountType.ASSET, parent_code="current")
            ledger.create_account("equity", "Equity", AccountType.EQUITY)

            from agent_ledger.models import JournalLine
            ledger.post_entry("Deposit", lines=[
                JournalLine(account_code="cash", debit=1000.0, credit=0.0),
                JournalLine(account_code="equity", debit=0.0, credit=1000.0),
            ])

            h = AccountHierarchy(ledger)
            # assets rollup includes current + cash
            rollup = h.get_rollup_balance("assets")
            assert rollup.balance == 1000.0


class TestAccountDeleteWithChildren:
    def test_delete_account_with_children_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test_ledger.json"
            storage = Storage(filepath)
            ledger = Ledger(storage)
            ledger._data = storage.init()

            ledger.create_account("assets", "Assets", AccountType.ASSET)
            ledger.create_account("cash", "Cash", AccountType.ASSET, parent_code="assets")

            with pytest.raises(AccountHasChildrenError):
                ledger.delete_account("assets")
