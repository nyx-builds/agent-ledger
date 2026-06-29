"""Tests for v0.5.0 features: tax reporting, general ledger, budget tracking, fiscal years."""

import pytest
import tempfile
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_ledger.ledger import Ledger
from agent_ledger.storage import Storage
from agent_ledger.models import AccountType, JournalLine
from agent_ledger.tax import generate_tax_summary, format_tax_summary, TaxCategory, DEFAULT_TAX_CATEGORIES
from agent_ledger.general_ledger import generate_general_ledger, format_general_ledger
from agent_ledger.budget import BudgetManager, BudgetError, BudgetNotFoundError, format_variance_report
from agent_ledger.fiscal import FiscalYearManager, FiscalYearError


@pytest.fixture
def ledger():
    """Create a ledger with sample accounts and entries."""
    import os
    tmpdir = tempfile.mkdtemp()
    filepath = Path(tmpdir) / "test_ledger.json"
    storage = Storage(filepath)
    ledger = Ledger(storage)
    ledger._data = storage.init()

    # Create accounts
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("revenue", "Sales Revenue", AccountType.REVENUE)
    ledger.create_account("rent", "Rent Expense", AccountType.EXPENSE)
    ledger.create_account("salary", "Salary Expense", AccountType.EXPENSE)
    ledger.create_account("payable", "Accounts Payable", AccountType.LIABILITY)

    # Post some entries
    ledger.post_entry(
        description="Sales income",
        lines=[
            JournalLine(account_code="cash", debit=10000.0, credit=0.0),
            JournalLine(account_code="revenue", debit=0.0, credit=10000.0),
        ],
    )
    ledger.post_entry(
        description="Rent payment",
        lines=[
            JournalLine(account_code="rent", debit=3000.0, credit=0.0),
            JournalLine(account_code="cash", debit=0.0, credit=3000.0),
        ],
    )
    ledger.post_entry(
        description="Salary payment",
        lines=[
            JournalLine(account_code="salary", debit=5000.0, credit=0.0),
            JournalLine(account_code="cash", debit=0.0, credit=5000.0),
        ],
    )

    return ledger


# ── Tax Reporting Tests ────────────────────────────────────────

class TestTaxReporting:
    def test_generate_tax_summary_basic(self, ledger):
        """Test basic tax summary generation."""
        report = generate_tax_summary(ledger)
        assert report.total_revenue == 10000.0
        assert report.total_deductible_expenses == 8000.0
        assert report.taxable_income == 2000.0

    def test_tax_summary_with_rate(self, ledger):
        """Test tax summary with estimated tax rate."""
        report = generate_tax_summary(ledger, tax_rate=0.21)
        assert report.tax_rate_used == 0.21
        assert report.estimated_tax == pytest.approx(2000.0 * 0.21, abs=0.01)

    def test_tax_summary_with_zero_rate(self, ledger):
        """Test tax summary with zero rate."""
        report = generate_tax_summary(ledger, tax_rate=0.0)
        assert report.estimated_tax == 0.0

    def test_tax_summary_items(self, ledger):
        """Test that tax items are classified correctly."""
        report = generate_tax_summary(ledger)
        assert len(report.items) > 0

        # Revenue item
        rev_items = [i for i in report.items if i.account_type == AccountType.REVENUE]
        assert len(rev_items) == 1
        assert rev_items[0].account_code == "revenue"
        assert rev_items[0].tax_code in ("INC", "OTHER")

        # Expense items
        exp_items = [i for i in report.items if i.account_type == AccountType.EXPENSE]
        assert len(exp_items) == 2

    def test_tax_summary_nondeductible(self, ledger):
        """Test non-deductible expense classification."""
        custom_cats = list(DEFAULT_TAX_CATEGORIES) + [
            TaxCategory(
                name="Non-Deductible",
                tax_code="NONDED",
                account_codes=["salary"],
                deductible=False,
            )
        ]
        report = generate_tax_summary(ledger, categories=custom_cats)
        # Salary should be non-deductible
        assert report.total_nondeductible_expenses == 5000.0
        assert report.total_deductible_expenses == 3000.0
        assert report.taxable_income == 7000.0  # 10000 - 3000

    def test_format_tax_summary(self, ledger):
        """Test text formatting of tax summary."""
        report = generate_tax_summary(ledger, tax_rate=0.21)
        text = format_tax_summary(report)
        assert "TAX SUMMARY" in text
        assert "Total Revenue" in text
        assert "Taxable Income" in text
        assert "Estimated Tax" in text

    def test_tax_summary_empty_ledger(self):
        """Test tax summary with no entries."""
        import os
        tmpdir = tempfile.mkdtemp()
        filepath = Path(tmpdir) / "empty_ledger.json"
        storage = Storage(filepath)
        ledger = Ledger(storage)
        ledger._data = storage.init()

        report = generate_tax_summary(ledger)
        assert report.total_revenue == 0.0
        assert report.taxable_income == 0.0
        assert len(report.items) == 0


# ── General Ledger Tests ───────────────────────────────────────

class TestGeneralLedger:
    def test_generate_general_ledger_basic(self, ledger):
        """Test basic General Ledger report generation."""
        report = generate_general_ledger(ledger)
        assert len(report.lines) > 0
        assert report.total_debits > 0
        assert report.total_credits > 0
        assert report.total_debits == report.total_credits  # Double-entry

    def test_general_ledger_filter_by_account(self, ledger):
        """Test filtering General Ledger by account."""
        report = generate_general_ledger(ledger, account_code="cash")
        assert all(l.account_code == "cash" for l in report.lines)

    def test_general_ledger_running_balance(self, ledger):
        """Test running balance calculation."""
        report = generate_general_ledger(ledger, account_code="cash")
        cash_lines = report.lines
        # Cash: +10000 (debit), -3000 (credit), -5000 (credit) = 2000
        if len(cash_lines) >= 1:
            # First cash entry: debit 10000, balance should be 10000
            assert cash_lines[0].running_balance == 10000.0

    def test_general_ledger_date_filter(self, ledger):
        """Test General Ledger with date filter."""
        from agent_ledger.reports import _ensure_aware
        now = datetime.now(timezone.utc)
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        report = generate_general_ledger(ledger, from_date=future)
        assert len(report.lines) == 0

    def test_format_general_ledger(self, ledger):
        """Test text formatting of General Ledger."""
        report = generate_general_ledger(ledger)
        text = format_general_ledger(report)
        assert "GENERAL LEDGER" in text
        assert "TOTALS" in text

    def test_general_ledger_empty_ledger(self):
        """Test General Ledger with no entries."""
        import os
        tmpdir = tempfile.mkdtemp()
        filepath = Path(tmpdir) / "empty_ledger.json"
        storage = Storage(filepath)
        ledger = Ledger(storage)
        ledger._data = storage.init()
        ledger.create_account("cash", "Cash", AccountType.ASSET)

        report = generate_general_ledger(ledger)
        assert len(report.lines) == 0
        assert report.total_debits == 0.0
        assert report.total_credits == 0.0


# ── Budget Tracking Tests ──────────────────────────────────────

class TestBudgetTracking:
    def test_create_budget(self, ledger):
        """Test creating a budget."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(
            name="Q1 Budget",
            budget_lines=[
                {"account_code": "revenue", "budgeted_amount": 15000.0},
                {"account_code": "rent", "budgeted_amount": 4000.0},
                {"account_code": "salary", "budgeted_amount": 6000.0},
            ],
        )
        assert budget.name == "Q1 Budget"
        assert budget.status == "draft"
        assert len(budget.lines) == 3
        assert budget.total_budgeted == 25000.0

    def test_create_budget_no_lines(self, ledger):
        """Test creating a budget without initial lines."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Empty Budget")
        assert budget.name == "Empty Budget"
        assert len(budget.lines) == 0

    def test_get_budget(self, ledger):
        """Test getting a budget by ID."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        retrieved = bm.get_budget(budget.id)
        assert retrieved.id == budget.id
        assert retrieved.name == "Test Budget"

    def test_get_budget_not_found(self, ledger):
        """Test getting a non-existent budget."""
        bm = BudgetManager(ledger)
        with pytest.raises(BudgetNotFoundError):
            bm.get_budget("nonexistent-id")

    def test_list_budgets(self, ledger):
        """Test listing budgets."""
        bm = BudgetManager(ledger)
        bm.create_budget(name="Budget 1")
        bm.create_budget(name="Budget 2")
        budgets = bm.list_budgets()
        assert len(budgets) == 2

    def test_list_budgets_by_status(self, ledger):
        """Test listing budgets filtered by status."""
        bm = BudgetManager(ledger)
        b1 = bm.create_budget(name="Draft Budget")
        b2 = bm.create_budget(name="Active Budget")
        bm.activate_budget(b2.id)

        drafts = bm.list_budgets(status="draft")
        assert len(drafts) == 1
        assert drafts[0].name == "Draft Budget"

        active = bm.list_budgets(status="active")
        assert len(active) == 1

    def test_activate_budget(self, ledger):
        """Test activating a draft budget."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        assert budget.status == "draft"

        activated = bm.activate_budget(budget.id)
        assert activated.status == "active"

    def test_activate_non_draft_budget(self, ledger):
        """Test activating a non-draft budget raises error."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        bm.activate_budget(budget.id)
        with pytest.raises(BudgetError):
            bm.activate_budget(budget.id)

    def test_close_budget(self, ledger):
        """Test closing an active budget."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        bm.activate_budget(budget.id)
        closed = bm.close_budget(budget.id)
        assert closed.status == "closed"

    def test_close_non_active_budget(self, ledger):
        """Test closing a non-active budget raises error."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        with pytest.raises(BudgetError):
            bm.close_budget(budget.id)

    def test_add_budget_line(self, ledger):
        """Test adding a budget line."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        line = bm.add_budget_line(budget.id, "revenue", 20000.0)
        assert line.account_code == "revenue"
        assert line.budgeted_amount == 20000.0

    def test_add_budget_line_duplicate_updates(self, ledger):
        """Test adding a duplicate account code updates the amount."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        bm.add_budget_line(budget.id, "revenue", 20000.0)
        line = bm.add_budget_line(budget.id, "revenue", 25000.0)
        assert line.budgeted_amount == 25000.0

        # Should still be only 1 line for revenue
        updated = bm.get_budget(budget.id)
        revenue_lines = [l for l in updated.lines if l.account_code == "revenue"]
        assert len(revenue_lines) == 1

    def test_add_budget_line_closed_budget(self, ledger):
        """Test adding a line to a closed budget raises error."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        bm.activate_budget(budget.id)
        bm.close_budget(budget.id)
        with pytest.raises(BudgetError):
            bm.add_budget_line(budget.id, "revenue", 10000.0)

    def test_remove_budget_line(self, ledger):
        """Test removing a budget line."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(
            name="Test Budget",
            budget_lines=[{"account_code": "revenue", "budgeted_amount": 10000.0}],
        )
        bm.remove_budget_line(budget.id, "revenue")
        updated = bm.get_budget(budget.id)
        assert len(updated.lines) == 0

    def test_delete_budget(self, ledger):
        """Test deleting a draft budget."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        bm.delete_budget(budget.id)
        with pytest.raises(BudgetNotFoundError):
            bm.get_budget(budget.id)

    def test_delete_active_budget_fails(self, ledger):
        """Test deleting an active budget raises error."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(name="Test Budget")
        bm.activate_budget(budget.id)
        with pytest.raises(BudgetError):
            bm.delete_budget(budget.id)

    def test_variance_report(self, ledger):
        """Test budget variance report."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(
            name="Q1 Budget",
            budget_lines=[
                {"account_code": "revenue", "budgeted_amount": 15000.0},
                {"account_code": "rent", "budgeted_amount": 4000.0},
            ],
        )
        report = bm.get_variance_report(budget.id)
        assert report.budget_name == "Q1 Budget"
        assert len(report.lines) == 2
        assert report.total_budgeted == 19000.0
        # Revenue actual = 10000, budget = 15000 => variance = +5000 (favorable for revenue)
        # Rent actual = 3000, budget = 4000 => variance = +1000 (favorable)

    def test_format_variance_report(self, ledger):
        """Test text formatting of variance report."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(
            name="Q1 Budget",
            budget_lines=[{"account_code": "revenue", "budgeted_amount": 15000.0}],
        )
        report = bm.get_variance_report(budget.id)
        text = format_variance_report(report)
        assert "BUDGET VARIANCE REPORT" in text
        assert "Q1 Budget" in text
        assert "Favorable" in text or "Unfavorable" in text or "On Budget" in text

    def test_budget_persistence(self, ledger):
        """Test that budgets are saved to and loaded from ledger data."""
        bm = BudgetManager(ledger)
        budget = bm.create_budget(
            name="Persistent Budget",
            budget_lines=[{"account_code": "revenue", "budgeted_amount": 12000.0}],
        )
        budget_id = budget.id

        # Create a new BudgetManager instance (simulating reload)
        bm2 = BudgetManager(ledger)
        retrieved = bm2.get_budget(budget_id)
        assert retrieved.name == "Persistent Budget"
        assert len(retrieved.lines) == 1


# ── Fiscal Year Tests ──────────────────────────────────────────

class TestFiscalYear:
    def test_create_fiscal_year_monthly(self, ledger):
        """Test creating a fiscal year with monthly periods."""
        fm = FiscalYearManager(ledger)
        fy = fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            period_type="month",
        )
        assert fy.name == "FY 2024"
        assert fy.status == "open"
        assert len(fy.periods) == 12

    def test_create_fiscal_year_quarterly(self, ledger):
        """Test creating a fiscal year with quarterly periods."""
        fm = FiscalYearManager(ledger)
        fy = fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            period_type="quarter",
        )
        assert len(fy.periods) == 4

    def test_create_fiscal_year_no_auto_periods(self, ledger):
        """Test creating a fiscal year without auto periods."""
        fm = FiscalYearManager(ledger)
        fy = fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            auto_periods=False,
        )
        assert len(fy.periods) == 0

    def test_list_fiscal_years(self, ledger):
        """Test listing fiscal years."""
        fm = FiscalYearManager(ledger)
        fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        fm.create_fiscal_year(
            name="FY 2025",
            start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
        )
        years = fm.list_fiscal_years()
        assert len(years) == 2

    def test_list_fiscal_years_by_status(self, ledger):
        """Test listing fiscal years filtered by status."""
        fm = FiscalYearManager(ledger)
        fy1 = fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        fm.close_fiscal_year(fy1.id)
        fm.create_fiscal_year(
            name="FY 2025",
            start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
        )
        open_years = fm.list_fiscal_years(status="open")
        assert len(open_years) == 1
        assert open_years[0].name == "FY 2025"

        closed_years = fm.list_fiscal_years(status="closed")
        assert len(closed_years) == 1

    def test_close_fiscal_year(self, ledger):
        """Test closing a fiscal year."""
        fm = FiscalYearManager(ledger)
        fy = fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        closed = fm.close_fiscal_year(fy.id)
        assert closed.status == "closed"
        # All periods should also be closed
        assert all(p.status == "closed" for p in closed.periods)

    def test_close_already_closed_fiscal_year(self, ledger):
        """Test closing an already closed fiscal year raises error."""
        fm = FiscalYearManager(ledger)
        fy = fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        fm.close_fiscal_year(fy.id)
        with pytest.raises(FiscalYearError):
            fm.close_fiscal_year(fy.id)

    def test_get_active_fiscal_year(self, ledger):
        """Test getting the currently active fiscal year."""
        fm = FiscalYearManager(ledger)
        now = datetime.now(timezone.utc)
        # Create a fiscal year that spans the current date
        fy = fm.create_fiscal_year(
            name="Current FY",
            start_date=datetime(now.year, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(now.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        )
        active = fm.get_active_fiscal_year()
        assert active is not None
        assert active.id == fy.id

    def test_get_active_fiscal_year_none(self, ledger):
        """Test getting active fiscal year when none exists."""
        fm = FiscalYearManager(ledger)
        active = fm.get_active_fiscal_year()
        assert active is None

    def test_fiscal_year_non_calendar(self, ledger):
        """Test creating a non-calendar fiscal year (e.g., Jul-Jun)."""
        fm = FiscalYearManager(ledger)
        fy = fm.create_fiscal_year(
            name="FY 2024-25",
            start_date=datetime(2024, 7, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 6, 30, tzinfo=timezone.utc),
            period_type="quarter",
        )
        assert fy.name == "FY 2024-25"
        assert len(fy.periods) == 4
        # First quarter: Jul 1 - Sep 30
        assert fy.periods[0].start_date.month == 7
        assert fy.periods[0].end_date.month == 9

    def test_fiscal_year_persistence(self, ledger):
        """Test fiscal year persistence."""
        fm = FiscalYearManager(ledger)
        fy = fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        fy_id = fy.id

        # Reload
        fm2 = FiscalYearManager(ledger)
        retrieved = fm2.get_fiscal_year(fy_id)
        assert retrieved.name == "FY 2024"
        assert len(retrieved.periods) == 12

    def test_fiscal_year_period_names_monthly(self, ledger):
        """Test that monthly period names are correct."""
        fm = FiscalYearManager(ledger)
        fy = fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            period_type="month",
        )
        assert fy.periods[0].name == "January 2024"
        assert fy.periods[11].name == "December 2024"

    def test_fiscal_year_period_names_quarterly(self, ledger):
        """Test that quarterly period names are correct."""
        fm = FiscalYearManager(ledger)
        fy = fm.create_fiscal_year(
            name="FY 2024",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            period_type="quarter",
        )
        assert "Q1" in fy.periods[0].name or "Quarter" in fy.periods[0].name
