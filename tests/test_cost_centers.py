"""Tests for cost centers / dimensional accounting (v0.9.0)."""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from agent_ledger.ledger import Ledger
from agent_ledger.models import AccountType
from agent_ledger.storage import Storage
from agent_ledger.cost_centers import (
    CostCenterManager,
    CostCenter,
    CostCenterReport,
    CostCenterSummary,
    format_cost_center_report,
    format_cost_center_summary,
)


@pytest.fixture
def ledger(tmp_path):
    """Create a ledger with accounts and entries."""
    storage = Storage(tmp_path / "test.json")
    ledger = Ledger(storage)
    ledger._data = storage.init(name="Test", base_currency="USD")

    # Create accounts
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("bank", "Bank Account", AccountType.ASSET)
    ledger.create_account("rev-product", "Product Revenue", AccountType.REVENUE)
    ledger.create_account("rev-service", "Service Revenue", AccountType.REVENUE)
    ledger.create_account("exp-materials", "Materials", AccountType.EXPENSE)
    ledger.create_account("exp-labor", "Labor", AccountType.EXPENSE)
    ledger.create_account("ap", "Accounts Payable", AccountType.LIABILITY)
    return ledger


@pytest.fixture
def mgr(ledger):
    return CostCenterManager(ledger)


@pytest.fixture
def populated_mgr(mgr, ledger):
    """Create cost centers and assign entries."""
    mgr.create("proj-alpha", "Project Alpha", "project")
    mgr.create("proj-beta", "Project Beta", "project")
    mgr.create("dept-eng", "Engineering Dept", "department")

    # Post entries and assign them
    # Project Alpha: 1000 revenue, 400 expenses = 600 net
    e1 = ledger.post_entry("Alpha sale", [("cash", 1000, 0), ("rev-product", 0, 1000)])
    mgr.assign_entry(e1.id, "proj-alpha")

    e2 = ledger.post_entry("Alpha materials", [("exp-materials", 400, 0), ("cash", 0, 400)])
    mgr.assign_entry(e2.id, "proj-alpha")

    # Project Beta: 500 revenue, 200 expenses = 300 net
    e3 = ledger.post_entry("Beta service", [("bank", 500, 0), ("rev-service", 0, 500)])
    mgr.assign_entry(e3.id, "proj-beta")

    e4 = ledger.post_entry("Beta labor", [("exp-labor", 200, 0), ("bank", 0, 200)])
    mgr.assign_entry(e4.id, "proj-beta")

    return mgr


class TestCostCenterCRUD:

    def test_create_cost_center(self, mgr):
        cc = mgr.create("proj-x", "Project X")
        assert cc.code == "proj-x"
        assert cc.name == "Project X"
        assert cc.center_type == "cost"
        assert cc.active is True

    def test_create_with_type(self, mgr):
        cc = mgr.create("dept-y", "Dept Y", center_type="department")
        assert cc.center_type == "department"

    def test_create_invalid_type(self, mgr):
        with pytest.raises(ValueError, match="Invalid center_type"):
            mgr.create("bad", "Bad", center_type="invalid")

    def test_create_duplicate(self, mgr):
        mgr.create("dup", "Duplicate")
        with pytest.raises(ValueError, match="already exists"):
            mgr.create("dup", "Another")

    def test_create_with_parent(self, mgr):
        mgr.create("parent", "Parent")
        child = mgr.create("child", "Child", parent_code="parent")
        assert child.parent_code == "parent"

    def test_create_with_invalid_parent(self, mgr):
        with pytest.raises(ValueError, match="Parent.*not found"):
            mgr.create("orphan", "Orphan", parent_code="nonexistent")

    def test_get_cost_center(self, mgr):
        mgr.create("test", "Test CC")
        cc = mgr.get("test")
        assert cc.name == "Test CC"

    def test_get_nonexistent(self, mgr):
        with pytest.raises(ValueError, match="not found"):
            mgr.get("nope")

    def test_list_all(self, mgr):
        mgr.create("a", "A")
        mgr.create("b", "B")
        mgr.create("c", "C")
        centers = mgr.list()
        assert len(centers) == 3

    def test_list_filtered_by_type(self, mgr):
        mgr.create("a", "A", center_type="project")
        mgr.create("b", "B", center_type="department")
        projects = mgr.list(center_type="project")
        assert len(projects) == 1
        assert projects[0].code == "a"

    def test_list_active_only(self, mgr):
        mgr.create("a", "A")
        mgr.create("b", "B")
        mgr.update("b", active=False)
        active = mgr.list(active_only=True)
        assert len(active) == 1
        assert active[0].code == "a"

    def test_update_name(self, mgr):
        mgr.create("test", "Original")
        cc = mgr.update("test", name="Updated")
        assert cc.name == "Updated"

    def test_update_description(self, mgr):
        mgr.create("test", "Test")
        cc = mgr.update("test", description="New desc")
        assert cc.description == "New desc"

    def test_update_active(self, mgr):
        mgr.create("test", "Test")
        mgr.update("test", active=False)
        cc = mgr.get("test")
        assert cc.active is False

    def test_update_tags(self, mgr):
        mgr.create("test", "Test")
        cc = mgr.update("test", tags=["important", "active"])
        assert "important" in cc.tags

    def test_update_nonexistent(self, mgr):
        with pytest.raises(ValueError, match="not found"):
            mgr.update("nope", name="X")

    def test_delete(self, mgr):
        mgr.create("test", "Test")
        mgr.delete("test")
        with pytest.raises(ValueError, match="not found"):
            mgr.get("test")

    def test_delete_with_children(self, mgr):
        mgr.create("parent", "Parent")
        mgr.create("child", "Child", parent_code="parent")
        with pytest.raises(ValueError, match="child cost centers"):
            mgr.delete("parent")

    def test_delete_with_entries(self, populated_mgr):
        with pytest.raises(ValueError, match="entries are assigned"):
            populated_mgr.delete("proj-alpha")

    def test_code_normalized_lower(self, mgr):
        cc = mgr.create("UPPER", "Upper Case")
        assert cc.code == "upper"


class TestEntryAssignment:

    def test_assign_entry(self, mgr, ledger):
        mgr.create("proj", "Project")
        entry = ledger.post_entry("Sale", [("cash", 100, 0), ("rev-product", 0, 100)])
        result = mgr.assign_entry(entry.id, "proj")
        assert result.metadata["cost_center"] == "proj"

    def test_assign_nonexistent_center(self, mgr, ledger):
        entry = ledger.post_entry("Sale", [("cash", 100, 0), ("rev-product", 0, 100)])
        with pytest.raises(ValueError, match="not found"):
            mgr.assign_entry(entry.id, "nonexistent")

    def test_unassign_entry(self, mgr, ledger):
        mgr.create("proj", "Project")
        entry = ledger.post_entry("Sale", [("cash", 100, 0), ("rev-product", 0, 100)])
        mgr.assign_entry(entry.id, "proj")
        result = mgr.unassign_entry(entry.id)
        assert "cost_center" not in result.metadata

    def test_unassign_not_assigned(self, mgr, ledger):
        entry = ledger.post_entry("Sale", [("cash", 100, 0), ("rev-product", 0, 100)])
        result = mgr.unassign_entry(entry.id)
        assert "cost_center" not in result.metadata

    def test_list_entries(self, populated_mgr):
        entries = populated_mgr.list_entries("proj-alpha")
        assert len(entries) == 2

    def test_list_entries_empty(self, populated_mgr):
        populated_mgr.create("empty", "Empty")
        entries = populated_mgr.list_entries("empty")
        assert len(entries) == 0


class TestCostCenterReport:

    def test_report_basic(self, populated_mgr):
        report = populated_mgr.report("proj-alpha")
        assert report.cost_center.code == "proj-alpha"
        assert report.total_revenue == 1000.0
        assert report.total_expenses == 400.0
        assert report.net_income == 600.0
        assert report.entry_count == 2

    def test_report_lines(self, populated_mgr):
        report = populated_mgr.report("proj-alpha")
        codes = [l.account_code for l in report.lines]
        assert "cash" in codes
        assert "rev-product" in codes
        assert "exp-materials" in codes

    def test_report_beta(self, populated_mgr):
        report = populated_mgr.report("proj-beta")
        assert report.total_revenue == 500.0
        assert report.total_expenses == 200.0
        assert report.net_income == 300.0

    def test_report_no_activity(self, populated_mgr):
        populated_mgr.create("empty", "Empty")
        report = populated_mgr.report("empty")
        assert report.entry_count == 0
        assert len(report.lines) == 0
        assert report.net_income == 0.0

    def test_report_with_date_filter(self, populated_mgr, ledger):
        # Create a future-dated entry
        future = datetime.now(timezone.utc) + timedelta(days=30)
        e5 = ledger.post_entry(
            "Future sale",
            [("cash", 200, 0), ("rev-product", 0, 200)],
            timestamp=future,
        )
        populated_mgr.assign_entry(e5.id, "proj-alpha")

        # Filter to now (excludes future entry)
        now = datetime.now(timezone.utc)
        report = populated_mgr.report("proj-alpha", to_date=now)
        assert report.entry_count == 2  # Original 2, not the future one

    def test_report_to_dict(self, populated_mgr):
        report = populated_mgr.report("proj-alpha")
        d = report.to_dict()
        assert d["cost_center"]["code"] == "proj-alpha"
        assert d["totals"]["revenue"] == 1000.0
        assert d["totals"]["net_income"] == 600.0
        assert d["entry_count"] == 2


class TestCostCenterSummary:

    def test_summary_totals(self, populated_mgr):
        summary = populated_mgr.summary()
        assert summary.total_revenue == 1500.0  # 1000 + 500
        assert summary.total_expenses == 600.0   # 400 + 200
        assert summary.total_net_income == 900.0

    def test_summary_center_count(self, populated_mgr):
        summary = populated_mgr.summary()
        # proj-alpha, proj-beta, dept-eng
        assert len(summary.centers) == 3

    def test_summary_unassigned(self, populated_mgr, ledger):
        # Post an unassigned entry
        ledger.post_entry("Unassigned", [("cash", 50, 0), ("rev-product", 0, 50)])
        summary = populated_mgr.summary()
        assert summary.unassigned_revenue == 50.0
        assert summary.unassigned_expenses == 0.0

    def test_summary_to_dict(self, populated_mgr):
        summary = populated_mgr.summary()
        d = summary.to_dict()
        assert d["totals"]["revenue"] == 1500.0
        assert len(d["centers"]) == 3


class TestCostCenterHierarchy:

    def test_hierarchy_flat(self, mgr):
        mgr.create("a", "A")
        mgr.create("b", "B")
        tree = mgr.get_hierarchy()
        assert len(tree) == 2

    def test_hierarchy_nested(self, mgr):
        mgr.create("parent", "Parent")
        mgr.create("child1", "Child 1", parent_code="parent")
        mgr.create("child2", "Child 2", parent_code="parent")
        mgr.create("grandchild", "Grandchild", parent_code="child1")
        tree = mgr.get_hierarchy()
        assert len(tree) == 1  # parent
        assert tree[0]["code"] == "parent"
        assert len(tree[0]["children"]) == 2
        # child1 has grandchild
        child1 = [c for c in tree[0]["children"] if c["code"] == "child1"][0]
        assert len(child1["children"]) == 1

    def test_hierarchy_subtree(self, mgr):
        mgr.create("parent", "Parent")
        mgr.create("child", "Child", parent_code="parent")
        mgr.create("other", "Other")
        subtree = mgr.get_hierarchy("parent")
        assert len(subtree) == 1
        assert subtree[0]["code"] == "child"


class TestFormatting:

    def test_format_report(self, populated_mgr):
        report = populated_mgr.report("proj-alpha")
        text = format_cost_center_report(report)
        assert "COST CENTER REPORT" in text
        assert "Project Alpha" in text
        assert "Total Revenue" in text
        assert "Net Income" in text

    def test_format_summary(self, populated_mgr):
        summary = populated_mgr.summary()
        text = format_cost_center_summary(summary)
        assert "COST CENTER SUMMARY" in text
        assert "TOTAL" in text
        assert "GRAND TOTAL" in text

    def test_format_empty_report(self, populated_mgr):
        populated_mgr.create("empty", "Empty")
        report = populated_mgr.report("empty")
        text = format_cost_center_report(report)
        assert "No activity" in text


class TestPersistence:

    def test_cost_centers_persist(self, ledger, mgr, tmp_path):
        mgr.create("persist-test", "Persistence Test")
        # Save and reload
        ledger.save()
        storage2 = Storage(tmp_path / "test.json")
        ledger2 = Ledger(storage2)
        mgr2 = CostCenterManager(ledger2)
        centers = mgr2.list()
        assert any(c.code == "persist-test" for c in centers)
