"""Tests for v0.7.0 features: recurring entries, financial ratios, CLI/MCP integration."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.ledger import Ledger
from agent_ledger.storage import Storage

from agent_ledger.recurring import (
    RecurringManager, RecurringEntry, RecurringLine,
    ScheduleType, compute_next_run,
    RecurringError, RecurringNotFoundError,
    format_recurring_list, format_recurring_detail,
)
from agent_ledger.ratios import (
    compute_ratios, format_ratios, get_financial_health, FinancialRatios,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def ledger(tmp_dir):
    """A ledger with a basic chart of accounts."""
    path = tmp_dir / "test.json"
    storage = Storage(path)
    storage.init(name="Test", base_currency="USD")
    ledger = Ledger(storage=storage)

    # Assets
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.get_account("cash").tags = ["cash", "current"]
    ledger.create_account("ar", "Accounts Receivable", AccountType.ASSET)
    ledger.get_account("ar").tags = ["current"]
    ledger.create_account("inventory", "Inventory", AccountType.ASSET)
    ledger.get_account("inventory").tags = ["inventory", "current"]
    ledger.create_account("equipment", "Equipment", AccountType.ASSET)

    # Liabilities
    ledger.create_account("ap", "Accounts Payable", AccountType.LIABILITY)
    ledger.get_account("ap").tags = ["current"]
    ledger.create_account("loan", "Long-term Loan", AccountType.LIABILITY)

    # Equity
    ledger.create_account("equity", "Owner's Equity", AccountType.EQUITY)

    # Revenue / Expense
    ledger.create_account("sales", "Sales Revenue", AccountType.REVENUE)
    ledger.create_account("cogs", "Cost of Goods Sold", AccountType.EXPENSE)
    ledger.create_account("rent", "Rent Expense", AccountType.EXPENSE)
    ledger.create_account("salary", "Salary Expense", AccountType.EXPENSE)

    ledger.save()
    return ledger


@pytest.fixture
def ledger_with_activity(ledger):
    """A ledger with posted entries for ratio testing."""
    # Initial capital
    ledger.post_entry("Owner investment", [
        ("cash", 50000, 0),
        ("equity", 0, 50000),
    ])
    # Buy equipment
    ledger.post_entry("Buy equipment", [
        ("equipment", 20000, 0),
        ("cash", 0, 20000),
    ])
    # Buy inventory
    ledger.post_entry("Buy inventory", [
        ("inventory", 10000, 0),
        ("cash", 0, 10000),
    ])
    # Make a sale on credit
    ledger.post_entry("Sale to customer", [
        ("ar", 15000, 0),
        ("sales", 0, 15000),
    ])
    # COGS
    ledger.post_entry("Record COGS", [
        ("cogs", 5000, 0),
        ("inventory", 0, 5000),
    ])
    # Collect from customer
    ledger.post_entry("Collect payment", [
        ("cash", 15000, 0),
        ("ar", 0, 15000),
    ])
    # Pay rent
    ledger.post_entry("Pay rent", [
        ("rent", 2000, 0),
        ("cash", 0, 2000),
    ])
    # Pay salaries
    ledger.post_entry("Pay salaries", [
        ("salary", 3000, 0),
        ("cash", 0, 3000),
    ])
    # Incur AP
    ledger.post_entry("Buy on credit", [
        ("inventory", 2000, 0),
        ("ap", 0, 2000),
    ])
    return ledger


# ── Recurring: Schedule computation tests ─────────────────────────

class TestComputeNextRun:
    def test_daily_schedule(self):
        after = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleType.DAILY, 1, after)
        assert result.day == 16
        assert result.hour == 0

    def test_daily_interval(self):
        after = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleType.DAILY, 3, after)
        assert result.day == 18

    def test_weekly_schedule(self):
        # Jan 15 2024 is a Monday
        after = datetime(2024, 1, 15, tzinfo=timezone.utc)  # Monday
        result = compute_next_run(ScheduleType.WEEKLY, 1, after, day_of_week=2)  # Wednesday
        assert result.weekday() == 2  # Wednesday
        assert result.day == 17

    def test_weekly_interval_2(self):
        after = datetime(2024, 1, 15, tzinfo=timezone.utc)  # Monday
        result = compute_next_run(ScheduleType.WEEKLY, 2, after, day_of_week=0)  # Monday
        # Next Monday + 1 more week = 2 weeks from now
        assert result.day == 29  # Jan 29

    def test_monthly_schedule(self):
        after = datetime(2024, 1, 15, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleType.MONTHLY, 1, after, day_of_month=1)
        assert result.month == 2
        assert result.day == 1

    def test_monthly_day_15(self):
        after = datetime(2024, 1, 10, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleType.MONTHLY, 1, after, day_of_month=15)
        assert result.day == 15

    def test_monthly_clamps_day(self):
        # Feb has 29 days in 2024
        after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleType.MONTHLY, 1, after, day_of_month=31)
        assert result.day == 29  # Clamped to Feb's last day

    def test_monthly_interval(self):
        after = datetime(2024, 1, 15, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleType.MONTHLY, 3, after, day_of_month=15)
        # After Jan → first candidate is Feb 15, which is > after
        # But interval=3 doesn't skip — it's the template cadence
        assert result.month == 2  # next month

    def test_quarterly_schedule(self):
        after = datetime(2024, 1, 15, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleType.QUARTERLY, 1, after, day_of_month=15)
        assert result.month == 4  # 3 months later
        assert result.day == 15

    def test_yearly_schedule(self):
        after = datetime(2024, 3, 15, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleType.YEARLY, 1, after, day_of_month=1, month_of_year=1)
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 1


# ── Recurring: Manager tests ──────────────────────────────────────

class TestRecurringManager:
    def test_create_template(self, ledger):
        rm = RecurringManager(ledger)
        template = rm.create(
            name="Monthly Rent",
            description="Office rent payment",
            lines=[
                {"account_code": "rent", "debit": 2000, "credit": 0},
                {"account_code": "cash", "debit": 0, "credit": 2000},
            ],
            schedule_type="monthly",
            day_of_month=1,
        )
        assert template.id
        assert template.name == "Monthly Rent"
        assert template.active is True
        assert template.occurrences_created == 0
        assert template.next_run is not None
        assert len(template.lines) == 2

    def test_create_requires_balanced_lines(self, ledger):
        rm = RecurringManager(ledger)
        with pytest.raises(RecurringError, match="do not balance"):
            rm.create(
                name="Bad",
                description="Unbalanced",
                lines=[
                    {"account_code": "rent", "debit": 2000, "credit": 0},
                    {"account_code": "cash", "debit": 0, "credit": 1000},
                ],
            )

    def test_create_requires_min_lines(self, ledger):
        rm = RecurringManager(ledger)
        with pytest.raises(RecurringError, match="2 lines"):
            rm.create(
                name="Bad",
                description="One line",
                lines=[
                    {"account_code": "rent", "debit": 2000, "credit": 0},
                ],
            )

    def test_create_validates_accounts(self, ledger):
        rm = RecurringManager(ledger)
        with pytest.raises(Exception, match="not found"):
            rm.create(
                name="Bad",
                description="Unknown account",
                lines=[
                    {"account_code": "nonexistent", "debit": 100, "credit": 0},
                    {"account_code": "cash", "debit": 0, "credit": 100},
                ],
            )

    def test_get_template(self, ledger):
        rm = RecurringManager(ledger)
        template = rm.create(
            name="Test",
            description="desc",
            lines=[
                {"account_code": "rent", "debit": 100, "credit": 0},
                {"account_code": "cash", "debit": 0, "credit": 100},
            ],
        )
        fetched = rm.get(template.id)
        assert fetched.name == "Test"

    def test_get_not_found(self, ledger):
        rm = RecurringManager(ledger)
        with pytest.raises(RecurringNotFoundError):
            rm.get("nonexistent-id")

    def test_list_templates(self, ledger):
        rm = RecurringManager(ledger)
        rm.create(name="T1", description="d", lines=[
            {"account_code": "rent", "debit": 100, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 100},
        ])
        rm.create(name="T2", description="d", lines=[
            {"account_code": "rent", "debit": 200, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 200},
        ])
        all_templates = rm.list_templates()
        assert len(all_templates) == 2

    def test_list_active_only(self, ledger):
        rm = RecurringManager(ledger)
        t1 = rm.create(name="T1", description="d", lines=[
            {"account_code": "rent", "debit": 100, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 100},
        ])
        rm.create(name="T2", description="d", lines=[
            {"account_code": "rent", "debit": 200, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 200},
        ])
        rm.pause(t1.id)
        active = rm.list_templates(active_only=True)
        assert len(active) == 1
        assert active[0].name == "T2"

    def test_pause_and_resume(self, ledger):
        rm = RecurringManager(ledger)
        template = rm.create(name="T", description="d", lines=[
            {"account_code": "rent", "debit": 100, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 100},
        ])
        assert template.active is True

        paused = rm.pause(template.id)
        assert paused.active is False

        resumed = rm.resume(template.id)
        assert resumed.active is True

    def test_update_template(self, ledger):
        rm = RecurringManager(ledger)
        template = rm.create(name="T", description="d", lines=[
            {"account_code": "rent", "debit": 100, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 100},
        ])
        updated = rm.update(template.id, name="Updated", description="New desc")
        assert updated.name == "Updated"
        assert updated.description == "New desc"

    def test_delete_template(self, ledger):
        rm = RecurringManager(ledger)
        template = rm.create(name="T", description="d", lines=[
            {"account_code": "rent", "debit": 100, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 100},
        ])
        rm.delete(template.id)
        with pytest.raises(RecurringNotFoundError):
            rm.get(template.id)

    def test_persistence_round_trip(self, ledger, tmp_dir):
        """Templates survive save/load."""
        rm = RecurringManager(ledger)
        rm.create(name="Persist", description="d", lines=[
            {"account_code": "rent", "debit": 500, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 500},
        ])

        # Reload ledger and manager
        storage2 = Storage(tmp_dir / "test.json")
        ledger2 = Ledger(storage=storage2)
        rm2 = RecurringManager(ledger2)
        templates = rm2.list_templates()
        assert len(templates) == 1
        assert templates[0].name == "Persist"


# ── Recurring: Generation tests ───────────────────────────────────

class TestRecurringGeneration:
    def test_is_due_not_due_yet(self, ledger):
        rm = RecurringManager(ledger)
        template = rm.create(
            name="T",
            description="d",
            lines=[
                {"account_code": "rent", "debit": 100, "credit": 0},
                {"account_code": "cash", "debit": 0, "credit": 100},
            ],
            start_date=datetime.now(timezone.utc) + timedelta(days=30),
        )
        assert rm.is_due(template) is False

    def test_is_due_past_start(self, ledger):
        rm = RecurringManager(ledger)
        past = datetime.now(timezone.utc) - timedelta(days=1)
        template = rm.create(
            name="T",
            description="d",
            lines=[
                {"account_code": "rent", "debit": 100, "credit": 0},
                {"account_code": "cash", "debit": 0, "credit": 100},
            ],
            start_date=past,
        )
        # next_run should be computed from start_date, which may be > now or <= now
        # Let's check: start_date is in the past, so next_run is computed after start_date
        # For monthly, next_run = start_date + 1 month, so it might be in the future
        # But compute_next_run with after=start_date where start_date is in the past
        # will return a date in the past too (start_date + 1 month can be < now)
        # Let's force next_run to the past
        template.next_run = datetime.now(timezone.utc) - timedelta(hours=1)
        assert rm.is_due(template) is True

    def test_generate_creates_entry(self, ledger):
        rm = RecurringManager(ledger)
        past = datetime.now(timezone.utc) - timedelta(days=2)
        template = rm.create(
            name="Monthly Rent",
            description="Rent for month",
            lines=[
                {"account_code": "rent", "debit": 2000, "credit": 0},
                {"account_code": "cash", "debit": 0, "credit": 2000},
            ],
            start_date=past,
            tags=["recurring"],
        )
        template.next_run = datetime.now(timezone.utc) - timedelta(hours=1)
        ledger.save()

        entry = rm.generate(template.id)
        assert entry is not None
        assert entry.description == "Rent for month"
        assert len(entry.lines) == 2
        assert "recurring" in entry.tags
        assert any("recurring:" in t for t in entry.tags)

        # Check ledger balance
        rent_balance = ledger.get_account_balance("rent")
        assert rent_balance.balance == 2000.0

        # Check template updated
        template2 = rm.get(template.id)
        assert template2.occurrences_created == 1
        assert template2.last_run is not None

    def test_generate_not_due_returns_none(self, ledger):
        rm = RecurringManager(ledger)
        template = rm.create(
            name="T",
            description="d",
            lines=[
                {"account_code": "rent", "debit": 100, "credit": 0},
                {"account_code": "cash", "debit": 0, "credit": 100},
            ],
            start_date=datetime.now(timezone.utc) + timedelta(days=30),
        )
        entry = rm.generate(template.id)
        assert entry is None

    def test_generate_respects_max_occurrences(self, ledger):
        rm = RecurringManager(ledger)
        past = datetime.now(timezone.utc) - timedelta(days=10)
        template = rm.create(
            name="T",
            description="d",
            lines=[
                {"account_code": "rent", "debit": 100, "credit": 0},
                {"account_code": "cash", "debit": 0, "credit": 100},
            ],
            start_date=past,
            max_occurrences=2,
        )
        # Force due
        template.next_run = datetime.now(timezone.utc) - timedelta(hours=2)
        ledger.save()

        # First occurrence
        e1 = rm.generate(template.id)
        assert e1 is not None

        # Force next run to past again
        template.next_run = datetime.now(timezone.utc) - timedelta(hours=1)
        ledger.save()

        # Second occurrence
        e2 = rm.generate(template.id)
        assert e2 is not None

        # Should be deactivated after hitting max
        assert rm.get(template.id).active is False

    def test_process_all(self, ledger):
        rm = RecurringManager(ledger)
        past = datetime.now(timezone.utc) - timedelta(days=5)

        t1 = rm.create(name="T1", description="First", lines=[
            {"account_code": "rent", "debit": 1000, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 1000},
        ], start_date=past)
        t2 = rm.create(name="T2", description="Second", lines=[
            {"account_code": "salary", "debit": 500, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 500},
        ], start_date=past)

        # Force both due
        for t in rm.list_templates():
            t.next_run = datetime.now(timezone.utc) - timedelta(hours=1)
        ledger.save()

        results = rm.process_all()
        assert len(results) == 2
        statuses = [r["status"] for r in results]
        assert all(s == "generated" for s in statuses)

        # Verify entries posted
        rent_balance = ledger.get_account_balance("rent")
        assert rent_balance.balance == 1000.0
        salary_balance = ledger.get_account_balance("salary")
        assert salary_balance.balance == 500.0

    def test_process_all_skips_not_due(self, ledger):
        rm = RecurringManager(ledger)
        future = datetime.now(timezone.utc) + timedelta(days=30)

        rm.create(name="NotDue", description="d", lines=[
            {"account_code": "rent", "debit": 100, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 100},
        ], start_date=future)

        results = rm.process_all()
        assert len(results) == 1
        assert results[0]["status"] == "not_due"

    def test_preview_next(self, ledger):
        rm = RecurringManager(ledger)
        template = rm.create(name="T", description="d", lines=[
            {"account_code": "rent", "debit": 100, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 100},
        ])
        next_run = rm.preview_next(template.id)
        assert next_run is not None


# ── Recurring: Format tests ───────────────────────────────────────

class TestRecurringFormatting:
    def test_format_list_empty(self):
        result = format_recurring_list([])
        assert "No recurring" in result

    def test_format_list_with_items(self, ledger):
        rm = RecurringManager(ledger)
        rm.create(name="Monthly Rent", description="d", lines=[
            {"account_code": "rent", "debit": 2000, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 2000},
        ])
        result = format_recurring_list(rm.list_templates())
        assert "Monthly Rent" in result
        assert "monthly" in result

    def test_format_detail(self, ledger):
        rm = RecurringManager(ledger)
        template = rm.create(
            name="Monthly Rent",
            description="Office rent",
            lines=[
                {"account_code": "rent", "debit": 2000, "credit": 0},
                {"account_code": "cash", "debit": 0, "credit": 2000},
            ],
            tags=["fixed"],
        )
        result = format_recurring_detail(template)
        assert "RECURRING ENTRY" in result
        assert "Monthly Rent" in result
        assert "Office rent" in result
        assert "rent" in result
        assert "fixed" in result


# ── Financial Ratios tests ────────────────────────────────────────

class TestFinancialRatios:
    def test_compute_ratios_basic(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.total_assets > 0
        assert ratios.total_revenue > 0
        assert ratios.net_income > 0

    def test_totals_correct(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)

        # Assets: cash(50000-20000-10000+15000-2000-3000=30000)
        # + equipment(20000) + inventory(10000-5000+2000=7000) + ar(0) = 57000
        assert ratios.total_assets == 57000.0

        # Liabilities: ap(2000) + loan(0) = 2000
        assert ratios.total_liabilities == 2000.0

        # Equity: 50000
        assert ratios.total_equity == 50000.0

        # Revenue: 15000
        assert ratios.total_revenue == 15000.0

        # Expenses: cogs(5000) + rent(2000) + salary(3000) = 10000
        assert ratios.total_expenses == 10000.0

        # Net income: 15000 - 10000 = 5000
        assert ratios.net_income == 5000.0

    def test_current_assets_identified(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        # Current assets: cash(30000) + ar(0) + inventory(7000) = 37000
        assert ratios.current_assets == 37000.0

    def test_cash_identified(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.cash_and_equivalents == 30000.0

    def test_inventory_identified(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.inventory_value == 7000.0

    def test_working_capital(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.working_capital == 37000.0 - 2000.0

    def test_current_ratio(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.current_ratio is not None
        assert ratios.current_ratio == round(37000 / 2000, 4)

    def test_quick_ratio(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.quick_ratio is not None
        assert ratios.quick_ratio == round((37000 - 7000) / 2000, 4)

    def test_cash_ratio(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.cash_ratio is not None
        assert ratios.cash_ratio == round(30000 / 2000, 4)

    def test_debt_to_equity(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.debt_to_equity is not None
        assert ratios.debt_to_equity == round(2000 / 50000, 4)

    def test_debt_to_assets(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.debt_to_assets is not None
        assert ratios.debt_to_assets == round(2000 / 57000, 4)

    def test_profit_margin(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.profit_margin is not None
        assert ratios.profit_margin == round(5000 / 15000, 4)

    def test_return_on_assets(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.return_on_assets is not None
        assert ratios.return_on_assets == round(5000 / 57000, 4)

    def test_return_on_equity(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.return_on_equity is not None
        assert ratios.return_on_equity == round(5000 / 50000, 4)

    def test_asset_turnover(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.asset_turnover is not None
        assert ratios.asset_turnover == round(15000 / 57000, 4)

    def test_empty_ledger_ratios_none(self, ledger):
        """Ratios should be None when denominators are zero."""
        ratios = compute_ratios(ledger)
        assert ratios.total_assets == 0
        assert ratios.current_ratio is None
        assert ratios.profit_margin is None
        assert len(ratios.warnings) > 0

    def test_as_of_date_filtering(self, ledger_with_activity):
        """Ratios with as_of date should only include entries up to that date."""
        # Use a date in the past before any entries
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        ratios = compute_ratios(ledger_with_activity, as_of=past)
        assert ratios.total_assets == 0
        assert ratios.total_revenue == 0

    def test_custom_tags(self, ledger_with_activity):
        """Test custom tag classification."""
        # Add a custom tag to equipment
        acct = ledger_with_activity.get_account("equipment")
        acct.tags = ["custom-liquid"]
        ledger_with_activity.save()

        ratios = compute_ratios(
            ledger_with_activity,
            cash_tags={"cash", "liquid", "bank", "custom-liquid"},
        )
        # Equipment should now be counted as cash alongside the cash account
        assert ratios.cash_and_equivalents == 30000 + 20000

    def test_format_ratios(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        text = format_ratios(ratios)
        assert "FINANCIAL RATIOS" in text
        assert "Current Ratio" in text
        assert "Profit Margin" in text
        assert "Total Assets" in text

    def test_format_ratios_with_none(self, ledger):
        """Format should handle None ratios gracefully."""
        ratios = compute_ratios(ledger)
        text = format_ratios(ratios)
        assert "N/A" in text

    def test_get_financial_health_healthy(self, ledger_with_activity):
        ratios = compute_ratios(ledger_with_activity)
        health = get_financial_health(ratios)
        assert "liquidity" in health
        assert health["liquidity"]["status"] == "healthy"
        assert "profitability" in health
        assert health["profitability"]["status"] == "healthy"

    def test_get_financial_health_at_risk(self, ledger):
        """A ledger with no activity should have missing health indicators."""
        ratios = compute_ratios(ledger)
        health = get_financial_health(ratios)
        # With no data, indicators won't be present
        assert "liquidity" not in health

    def test_loss_makes_profitability_at_risk(self, ledger_with_activity):
        """Post a large expense to make the ledger operate at a loss."""
        ledger_with_activity.post_entry("Huge expense", [
            ("rent", 20000, 0),
            ("cash", 0, 20000),
        ])
        ratios = compute_ratios(ledger_with_activity)
        assert ratios.net_income < 0
        health = get_financial_health(ratios)
        assert health["profitability"]["status"] == "at_risk"


# ── SQLite integration for recurring entries ──────────────────────

class TestRecurringWithSQLite:
    def test_sqlite_persistence(self, tmp_dir):
        from agent_ledger.sqlite_storage import SQLiteStorage
        path = tmp_dir / "test.db"
        storage = SQLiteStorage(path)
        storage.init()
        ledger = Ledger(storage=storage)
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        ledger.create_account("rent", "Rent", AccountType.EXPENSE)

        rm = RecurringManager(ledger)
        rm.create(name="Rent", description="Monthly rent", lines=[
            {"account_code": "rent", "debit": 1000, "credit": 0},
            {"account_code": "cash", "debit": 0, "credit": 1000},
        ])

        # Reload
        storage2 = SQLiteStorage(path)
        ledger2 = Ledger(storage=storage2)
        rm2 = RecurringManager(ledger2)
        templates = rm2.list_templates()
        assert len(templates) == 1
        assert templates[0].name == "Rent"
