"""Tests for v0.8.0 features: aging reports and depreciation engine."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_ledger.models import AccountType
from agent_ledger.ledger import Ledger
from agent_ledger.storage import Storage

from agent_ledger.aging import (
    generate_aging_report, format_aging_report, aging_summary_dict,
    AgingReportType, AgingReport, AgingError,
)
from agent_ledger.depreciation import (
    DepreciationManager, FixedAsset, DepreciationMethod,
    DepreciationError, AssetNotFoundError,
    format_asset_list, format_asset_detail, format_depreciation_schedule,
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
    ledger.create_account("ar", "Accounts Receivable", AccountType.ASSET)
    ledger.create_account("equipment", "Equipment", AccountType.ASSET)
    ledger.create_account("accum_dep", "Accumulated Depreciation", AccountType.ASSET)
    ledger.create_account("inventory", "Inventory", AccountType.ASSET)

    # Liabilities
    ledger.create_account("ap", "Accounts Payable", AccountType.LIABILITY)

    # Equity
    ledger.create_account("equity", "Owner's Equity", AccountType.EQUITY)

    # Revenue / Expense
    ledger.create_account("sales", "Sales Revenue", AccountType.REVENUE)
    ledger.create_account("dep_expense", "Depreciation Expense", AccountType.EXPENSE)
    ledger.create_account("cogs", "Cost of Goods Sold", AccountType.EXPENSE)

    ledger.save()
    return ledger


# ════════════════════════════════════════════════════════════════════
#  AGING REPORTS
# ════════════════════════════════════════════════════════════════════


class TestAgingReportBasics:
    def test_empty_ledger_no_outstanding(self, ledger):
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        assert report.total_outstanding == 0.0
        assert len(report.warnings) > 0

    def test_requires_account_codes(self, ledger):
        with pytest.raises(AgingError, match="account code"):
            generate_aging_report(ledger, [], AgingReportType.RECEIVABLE)

    def test_invalid_account_raises(self, ledger):
        with pytest.raises(Exception, match="not found"):
            generate_aging_report(
                ledger, ["nonexistent"], AgingReportType.RECEIVABLE
            )

    def test_report_type_stored(self, ledger):
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        assert report.report_type == AgingReportType.RECEIVABLE

    def test_default_buckets(self, ledger):
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        labels = [b.label for b in report.buckets]
        assert labels == ["0-30", "31-60", "61-90", "90+"]

    def test_custom_buckets(self, ledger):
        custom = [("0-15", 0, 15), ("16-30", 16, 30), ("30+", 31, None)]
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE,
            bucket_defs=custom,
        )
        labels = [b.label for b in report.buckets]
        assert labels == ["0-15", "16-30", "30+"]


class TestAgingReceivable:
    def test_single_recent_invoice(self, ledger):
        """A recent invoice should be in the 0-30 bucket."""
        # Fund AR: sell on credit
        ledger.post_entry("Sale on credit", [
            ("ar", 5000, 0),
            ("sales", 0, 5000),
        ])
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        assert report.total_outstanding == 5000.0
        # Should be in 0-30 bucket
        assert report.buckets[0].label == "0-30"
        assert report.buckets[0].total == 5000.0
        assert len(report.buckets[0].items) == 1

    def test_old_invoice_in_90_plus_bucket(self, ledger):
        """An invoice from 100+ days ago should be in the 90+ bucket."""
        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        ledger.post_entry("Old sale on credit", [
            ("ar", 3000, 0),
            ("sales", 0, 3000),
        ], timestamp=old_date)

        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        assert report.total_outstanding == 3000.0
        bucket_90 = [b for b in report.buckets if b.label == "90+"][0]
        assert bucket_90.total == 3000.0
        assert len(bucket_90.items) == 1
        assert bucket_90.items[0].days_outstanding >= 90

    def test_invoice_in_31_60_bucket(self, ledger):
        """An invoice from 45 days ago should be in the 31-60 bucket."""
        date = datetime.now(timezone.utc) - timedelta(days=45)
        ledger.post_entry("Mid-old sale", [
            ("ar", 2000, 0),
            ("sales", 0, 2000),
        ], timestamp=date)

        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        bucket = [b for b in report.buckets if b.label == "31-60"][0]
        assert bucket.total == 2000.0

    def test_invoice_in_61_90_bucket(self, ledger):
        """An invoice from 75 days ago should be in the 61-90 bucket."""
        date = datetime.now(timezone.utc) - timedelta(days=75)
        ledger.post_entry("Old sale", [
            ("ar", 1500, 0),
            ("sales", 0, 1500),
        ], timestamp=date)

        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        bucket = [b for b in report.buckets if b.label == "61-90"][0]
        assert bucket.total == 1500.0

    def test_payment_reduces_outstanding(self, ledger):
        """A payment should reduce the outstanding AR balance."""
        ledger.post_entry("Sale", [
            ("ar", 10000, 0),
            ("sales", 0, 10000),
        ])
        # Partial payment
        ledger.post_entry("Partial payment received", [
            ("cash", 4000, 0),
            ("ar", 0, 4000),
        ])

        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        assert report.total_outstanding == 6000.0

    def test_full_payment_clears_outstanding(self, ledger):
        """A full payment should clear the outstanding balance."""
        ledger.post_entry("Sale", [
            ("ar", 5000, 0),
            ("sales", 0, 5000),
        ])
        ledger.post_entry("Full payment received", [
            ("cash", 5000, 0),
            ("ar", 0, 5000),
        ])

        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        assert report.total_outstanding == 0.0

    def test_fifo_payment_applied_to_oldest_first(self, ledger):
        """Payments should reduce the oldest entries first (FIFO)."""
        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        recent_date = datetime.now(timezone.utc) - timedelta(days=5)

        # Two invoices: old and recent
        ledger.post_entry("Old invoice", [
            ("ar", 3000, 0), ("sales", 0, 3000),
        ], timestamp=old_date)
        ledger.post_entry("Recent invoice", [
            ("ar", 2000, 0), ("sales", 0, 2000),
        ], timestamp=recent_date)

        # Pay 3000 — should clear the old invoice entirely
        ledger.post_entry("Payment", [
            ("cash", 3000, 0), ("ar", 0, 3000),
        ], timestamp=datetime.now(timezone.utc))

        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        assert report.total_outstanding == 2000.0

        # The remaining 2000 should be in 0-30 bucket (the recent invoice)
        bucket_recent = [b for b in report.buckets if b.label == "0-30"][0]
        assert bucket_recent.total == 2000.0

        # The 90+ bucket should be empty (old invoice fully paid)
        bucket_old = [b for b in report.buckets if b.label == "90+"][0]
        assert bucket_old.total == 0.0

    def test_multiple_invoices_spread_across_buckets(self, ledger):
        """Multiple invoices at different ages land in different buckets."""
        now = datetime.now(timezone.utc)
        ages = [10, 45, 75, 100]
        amounts = [1000, 2000, 3000, 4000]

        for age, amt in zip(ages, amounts):
            ledger.post_entry(f"Invoice {age}d old", [
                ("ar", amt, 0), ("sales", 0, amt),
            ], timestamp=now - timedelta(days=age))

        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        assert report.total_outstanding == 10000.0

        # Each bucket should have the expected amount
        for b in report.buckets:
            if b.label == "0-30":
                assert b.total == 1000.0
            elif b.label == "31-60":
                assert b.total == 2000.0
            elif b.label == "61-90":
                assert b.total == 3000.0
            elif b.label == "90+":
                assert b.total == 4000.0


class TestAgingPayable:
    def test_payable_uses_credit_side(self, ledger):
        """AP increases on credit, decreases on debit."""
        ledger.post_entry("Purchase on credit", [
            ("inventory", 3000, 0),
            ("ap", 0, 3000),
        ])

        report = generate_aging_report(
            ledger, ["ap"], AgingReportType.PAYABLE
        )
        assert report.total_outstanding == 3000.0

    def test_payable_payment_reduces_balance(self, ledger):
        """Paying a payable reduces the outstanding AP."""
        ledger.post_entry("Purchase on credit", [
            ("inventory", 5000, 0),
            ("ap", 0, 5000),
        ])
        ledger.post_entry("Payment made", [
            ("ap", 2000, 0),
            ("cash", 0, 2000),
        ])

        report = generate_aging_report(
            ledger, ["ap"], AgingReportType.PAYABLE
        )
        assert report.total_outstanding == 3000.0


class TestAgingAsOfDate:
    def test_as_of_date_filtering(self, ledger):
        """Using an as_of date before any entries should show nothing."""
        ledger.post_entry("Sale", [
            ("ar", 5000, 0), ("sales", 0, 5000),
        ])
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE,
            as_of=past,
        )
        assert report.total_outstanding == 0.0


class TestAgingFormatting:
    def test_format_empty_report(self, ledger):
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        text = format_aging_report(report)
        assert "ACCOUNTS RECEIVABLE" in text
        assert "AGING REPORT" in text

    def test_format_with_data(self, ledger):
        ledger.post_entry("Sale", [
            ("ar", 5000, 0), ("sales", 0, 5000),
        ])
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        text = format_aging_report(report)
        assert "ACCOUNTS RECEIVABLE" in text
        assert "5,000.00" in text

    def test_format_with_details(self, ledger):
        ledger.post_entry("Sale to customer", [
            ("ar", 5000, 0), ("sales", 0, 5000),
        ])
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        text = format_aging_report(report, show_details=True)
        assert "DETAILS BY BUCKET" in text
        assert "Sale to customer" in text

    def test_format_payable(self, ledger):
        ledger.post_entry("Purchase", [
            ("inventory", 3000, 0), ("ap", 0, 3000),
        ])
        report = generate_aging_report(
            ledger, ["ap"], AgingReportType.PAYABLE
        )
        text = format_aging_report(report)
        assert "ACCOUNTS PAYABLE" in text

    def test_summary_dict(self, ledger):
        ledger.post_entry("Sale", [
            ("ar", 5000, 0), ("sales", 0, 5000),
        ])
        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        summary = aging_summary_dict(report)
        assert summary["report_type"] == "receivable"
        assert summary["total_outstanding"] == 5000.0
        assert len(summary["buckets"]) == 4
        assert summary["item_count"] == 1

    def test_warning_for_old_items(self, ledger):
        old_date = datetime.now(timezone.utc) - timedelta(days=120)
        ledger.post_entry("Very old sale", [
            ("ar", 1000, 0), ("sales", 0, 1000),
        ], timestamp=old_date)

        report = generate_aging_report(
            ledger, ["ar"], AgingReportType.RECEIVABLE
        )
        assert any("90 days" in w for w in report.warnings)


# ════════════════════════════════════════════════════════════════════
#  DEPRECIATION
# ════════════════════════════════════════════════════════════════════


class TestDepreciationManagerBasics:
    def test_create_asset_straight_line(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000,
            salvage_value=2000,
            useful_life_months=60,
            method="straight_line",
        )
        assert asset.id
        assert asset.name == "Server"
        assert asset.cost == 12000.0
        assert asset.salvage_value == 2000.0
        assert asset.useful_life_months == 60
        assert asset.method == DepreciationMethod.STRAIGHT_LINE
        assert asset.active is True
        assert asset.accumulated_depreciation == 0.0
        assert asset.book_value == 12000.0

    def test_create_asset_validates_cost(self, ledger):
        dm = DepreciationManager(ledger)
        with pytest.raises(DepreciationError, match="Cost must be positive"):
            dm.create_asset(
                name="Bad",
                asset_account="equipment",
                accum_dep_account="accum_dep",
                dep_expense_account="dep_expense",
                cost=-100,
                salvage_value=0,
                useful_life_months=60,
            )

    def test_create_asset_validates_salvage(self, ledger):
        dm = DepreciationManager(ledger)
        with pytest.raises(DepreciationError, match="Salvage value cannot"):
            dm.create_asset(
                name="Bad",
                asset_account="equipment",
                accum_dep_account="accum_dep",
                dep_expense_account="dep_expense",
                cost=10000,
                salvage_value=15000,
                useful_life_months=60,
            )

    def test_create_asset_validates_accounts(self, ledger):
        dm = DepreciationManager(ledger)
        with pytest.raises(Exception, match="not found"):
            dm.create_asset(
                name="Bad",
                asset_account="nonexistent",
                accum_dep_account="accum_dep",
                dep_expense_account="dep_expense",
                cost=10000,
                salvage_value=0,
                useful_life_months=60,
            )

    def test_create_asset_requires_life_for_time_based(self, ledger):
        dm = DepreciationManager(ledger)
        with pytest.raises(DepreciationError, match="Useful life"):
            dm.create_asset(
                name="Bad",
                asset_account="equipment",
                accum_dep_account="accum_dep",
                dep_expense_account="dep_expense",
                cost=10000,
                salvage_value=0,
                useful_life_months=0,
                method="straight_line",
            )


class TestStraightLineDepreciation:
    def test_straight_line_monthly_amount(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Equipment",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000,
            salvage_value=2000,
            useful_life_months=60,
        )
        # (12000 - 2000) / 60 = 166.67
        assert asset.straight_line_monthly == round(10000 / 60, 2)

    def test_post_depreciation_creates_entry(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000,
            salvage_value=2000,
            useful_life_months=60,
        )
        dep = dm.post_depreciation(asset.id)
        assert dep is not None
        assert dep.amount == round(10000 / 60, 2)
        assert dep.method == DepreciationMethod.STRAIGHT_LINE
        assert dep.entry_id is not None

        # Check the asset's book value updated
        asset2 = dm.get(asset.id)
        assert asset2.accumulated_depreciation == round(10000 / 60, 2)
        assert asset2.book_value == round(12000 - 10000 / 60, 2)
        assert asset2.months_depreciated == 1

    def test_post_multiple_periods(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000,
            salvage_value=2000,
            useful_life_months=60,
        )
        for _ in range(3):
            dm.post_depreciation(asset.id)

        asset2 = dm.get(asset.id)
        monthly = round(10000 / 60, 2)
        assert asset2.accumulated_depreciation == round(monthly * 3, 2)
        assert asset2.months_depreciated == 3

    def test_post_creates_journal_entry(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000,
            salvage_value=0,
            useful_life_months=120,  # 10 years
        )
        dep = dm.post_depreciation(asset.id)
        entry = ledger.get_entry(dep.entry_id)
        assert "Depreciation" in entry.description
        assert entry.lines[0].account_code == "dep_expense"
        assert entry.lines[0].debit > 0
        assert entry.lines[1].account_code == "accum_dep"
        assert entry.lines[1].credit > 0

    def test_fully_depreciated_becomes_inactive(self, ledger):
        dm = DepreciationManager(ledger)
        # Small asset with short life
        asset = dm.create_asset(
            name="Small Asset",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=120,
            salvage_value=0,
            useful_life_months=12,  # 10/month
        )
        for _ in range(12):
            dm.post_depreciation(asset.id)

        asset2 = dm.get(asset.id)
        assert asset2.is_fully_depreciated is True
        assert asset2.active is False
        assert asset2.book_value == 0.0

    def test_no_depreciation_when_inactive(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Test",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=1200,
            salvage_value=0,
            useful_life_months=12,
        )
        dm.update(asset.id, active=False)
        result = dm.post_depreciation(asset.id)
        assert result is None


class TestDecliningBalanceDepreciation:
    def test_double_declining_first_period(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Equipment",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=10000,
            salvage_value=1000,
            useful_life_months=60,  # 5 years
            method="double_declining",
        )
        # Double-declining rate = 2/60 = 0.0333 per month
        # First month: 10000 * (2/60) = 333.33
        dep = dm.post_depreciation(asset.id)
        assert dep is not None
        # With optimal switch, SL might be higher in early periods
        # SL = (10000 - 1000) / 60 = 150
        # DDB = 10000 * (2/60) = 333.33
        # DDB > SL, so DDB is used
        assert dep.amount == round(10000 * 2 / 60, 2)

    def test_declining_balance_with_custom_rate(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Equipment",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=10000,
            salvage_value=0,
            useful_life_months=60,
            method="declining_balance",
            declining_rate=0.05,  # 5% per month
        )
        dep = dm.post_depreciation(asset.id)
        # 10000 * 0.05 = 500
        assert dep.amount == 500.0

    def test_declining_balance_decreases_over_time(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Equipment",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=10000,
            salvage_value=0,
            useful_life_months=60,
            method="declining_balance",
            declining_rate=0.05,
        )
        dep1 = dm.post_depreciation(asset.id)
        dep2 = dm.post_depreciation(asset.id)
        # First: 10000 * 0.05 = 500
        # Second: (10000-500) * 0.05 = 475
        assert dep1.amount == 500.0
        # Second could be DDB or SL (whichever higher)
        # DDB: 9500 * 0.05 = 475, SL: 9500/59 = 161.01 → DDB wins
        assert dep2.amount == 475.0


class TestUnitsOfProduction:
    def test_create_uop_asset(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Machine",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=50000,
            salvage_value=5000,
            useful_life_months=120,
            method="units_of_production",
            total_units=100000,
        )
        assert asset.method == DepreciationMethod.UNITS_OF_PRODUCTION
        assert asset.total_units == 100000

    def test_post_units(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Machine",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=50000,
            salvage_value=5000,
            useful_life_months=120,
            method="units_of_production",
            total_units=100000,
        )
        # Rate = (50000 - 5000) / 100000 = 0.45 per unit
        dep = dm.post_units(asset.id, units=1000)
        assert dep is not None
        assert dep.amount == 450.0  # 1000 * 0.45
        assert dep.units == 1000

        asset2 = dm.get(asset.id)
        assert asset2.units_consumed == 1000
        assert asset2.accumulated_depreciation == 450.0

    def test_post_units_requires_uop_method(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=10000,
            salvage_value=0,
            useful_life_months=60,
            method="straight_line",
        )
        with pytest.raises(DepreciationError, match="units-of-production"):
            dm.post_units(asset.id, units=100)

    def test_uop_requires_total_units(self, ledger):
        dm = DepreciationManager(ledger)
        with pytest.raises(DepreciationError, match="total_units"):
            dm.create_asset(
                name="Bad",
                asset_account="equipment",
                accum_dep_account="accum_dep",
                dep_expense_account="dep_expense",
                cost=10000,
                salvage_value=0,
                useful_life_months=120,
                method="units_of_production",
            )


class TestDepreciationManagerCRUD:
    def test_get_asset(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Test",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=5000,
            salvage_value=0,
            useful_life_months=60,
        )
        fetched = dm.get(asset.id)
        assert fetched.name == "Test"

    def test_get_not_found(self, ledger):
        dm = DepreciationManager(ledger)
        with pytest.raises(AssetNotFoundError):
            dm.get("nonexistent-id")

    def test_list_assets(self, ledger):
        dm = DepreciationManager(ledger)
        dm.create_asset(
            name="A1",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=5000, salvage_value=0, useful_life_months=60,
        )
        dm.create_asset(
            name="A2",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=3000, salvage_value=0, useful_life_months=36,
        )
        assert len(dm.list_assets()) == 2

    def test_list_active_only(self, ledger):
        dm = DepreciationManager(ledger)
        a1 = dm.create_asset(
            name="A1",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=5000, salvage_value=0, useful_life_months=60,
        )
        dm.create_asset(
            name="A2",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=3000, salvage_value=0, useful_life_months=36,
        )
        dm.update(a1.id, active=False)
        active = dm.list_assets(active_only=True)
        assert len(active) == 1
        assert active[0].name == "A2"

    def test_update_asset(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Test",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=5000, salvage_value=500, useful_life_months=60,
        )
        updated = dm.update(asset.id, name="Updated", salvage_value=1000)
        assert updated.name == "Updated"
        assert updated.salvage_value == 1000.0

    def test_delete_asset(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Test",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=5000, salvage_value=0, useful_life_months=60,
        )
        dm.delete(asset.id)
        with pytest.raises(AssetNotFoundError):
            dm.get(asset.id)


class TestDepreciationPersistence:
    def test_persistence_round_trip(self, ledger, tmp_dir):
        dm = DepreciationManager(ledger)
        dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000, salvage_value=2000, useful_life_months=60,
        )

        # Reload
        storage2 = Storage(tmp_dir / "test.json")
        ledger2 = Ledger(storage=storage2)
        dm2 = DepreciationManager(ledger2)
        assets = dm2.list_assets()
        assert len(assets) == 1
        assert assets[0].name == "Server"

    def test_history_persisted(self, ledger, tmp_dir):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000, salvage_value=0, useful_life_months=120,
        )
        dm.post_depreciation(asset.id)
        dm.post_depreciation(asset.id)

        # Reload
        storage2 = Storage(tmp_dir / "test.json")
        ledger2 = Ledger(storage=storage2)
        dm2 = DepreciationManager(ledger2)
        asset2 = dm2.list_assets()[0]
        assert asset2.months_depreciated == 2
        assert asset2.accumulated_depreciation > 0

    def test_sqlite_persistence(self, tmp_dir):
        from agent_ledger.sqlite_storage import SQLiteStorage
        path = tmp_dir / "test.db"
        storage = SQLiteStorage(path)
        storage.init()
        ledger = Ledger(storage=storage)
        ledger.create_account("equipment", "Equipment", AccountType.ASSET)
        ledger.create_account("accum_dep", "Acc Dep", AccountType.ASSET)
        ledger.create_account("dep_exp", "Dep Expense", AccountType.EXPENSE)

        dm = DepreciationManager(ledger)
        dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_exp",
            cost=10000, salvage_value=0, useful_life_months=60,
        )

        storage2 = SQLiteStorage(path)
        ledger2 = Ledger(storage=storage2)
        dm2 = DepreciationManager(ledger2)
        assert len(dm2.list_assets()) == 1


class TestPostAllDepreciation:
    def test_post_all_active(self, ledger):
        dm = DepreciationManager(ledger)
        dm.create_asset(
            name="A1",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=6000, salvage_value=0, useful_life_months=60,
        )
        dm.create_asset(
            name="A2",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000, salvage_value=2000, useful_life_months=60,
        )
        results = dm.post_all_depreciation()
        assert len(results) == 2
        statuses = [r["status"] for r in results]
        assert all(s == "posted" for s in statuses)
        # A1: 6000/60 = 100; A2: 10000/60 = 166.67
        amounts = {r["asset_name"]: r["amount"] for r in results}
        assert amounts["A1"] == 100.0
        assert amounts["A2"] == round(10000 / 60, 2)

    def test_post_all_skips_inactive(self, ledger):
        dm = DepreciationManager(ledger)
        a1 = dm.create_asset(
            name="Active",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=6000, salvage_value=0, useful_life_months=60,
        )
        dm.create_asset(
            name="Inactive",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=6000, salvage_value=0, useful_life_months=60,
        )
        dm.update(a1.id, active=False)
        results = dm.post_all_depreciation()
        statuses = {r["asset_name"]: r["status"] for r in results}
        assert statuses["Active"] == "skipped"
        assert statuses["Inactive"] == "posted"


class TestAssetDisposal:
    def test_disposal_with_loss(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000, salvage_value=0, useful_life_months=12,
        )
        # Depreciate a few months — 1000/month for 12 months = 12000 total
        # After 4 months: accum = 4000, book = 8000
        for _ in range(4):
            dm.post_depreciation(asset.id)

        # Book value = 12000 - 4000 = 8000
        asset2 = dm.get(asset.id)
        assert asset2.book_value == 8000.0

        # Dispose for 5000 — loss of 3000
        result = dm.dispose(asset.id, disposal_value=5000, disposal_account="cash")
        assert result["gain_or_loss"] == -3000.0
        assert result["gain"] is False
        assert result["book_value"] == 8000.0
        assert result["disposal_value"] == 5000.0

        # Asset should be inactive
        asset3 = dm.get(asset.id)
        assert asset3.active is False
        assert asset3.disposal_date is not None

    def test_disposal_with_gain(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Land Improvement",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=6000, salvage_value=0, useful_life_months=60,
        )
        # Depreciate fully
        for _ in range(60):
            dm.post_depreciation(asset.id)

        # Book value = 0, sell for 1000 → gain of 1000
        result = dm.dispose(asset.id, disposal_value=1000, disposal_account="cash")
        assert result["gain_or_loss"] == 1000.0
        assert result["gain"] is True

    def test_disposal_at_book_value(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Equipment",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=6000, salvage_value=0, useful_life_months=12,
        )
        for _ in range(6):
            dm.post_depreciation(asset.id)

        book = dm.get(asset.id).book_value  # 6000 - 3000 = 3000
        result = dm.dispose(asset.id, disposal_value=book, disposal_account="cash")
        assert result["gain_or_loss"] == 0.0

    def test_cannot_dispose_twice(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Test",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=5000, salvage_value=0, useful_life_months=60,
        )
        dm.dispose(asset.id, disposal_value=0)
        with pytest.raises(DepreciationError, match="already disposed"):
            dm.dispose(asset.id, disposal_value=0)


class TestDepreciationSchedule:
    def test_schedule_straight_line(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Equipment",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000, salvage_value=2000, useful_life_months=12,
        )
        schedule = dm.get_schedule(asset.id)
        assert len(schedule) == 12
        monthly = round(10000 / 12, 2)
        # All periods except the last should use the standard monthly amount
        for row in schedule[:-1]:
            assert row["depreciation"] == monthly

        # Last period should reach salvage value exactly
        assert schedule[-1]["book_value"] == 2000.0

    def test_schedule_stops_at_salvage(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Equipment",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=1200, salvage_value=200, useful_life_months=10,
        )
        # Request more periods than useful life
        schedule = dm.get_schedule(asset.id, periods=20)
        # Should stop at 10 periods (salvage reached)
        assert len(schedule) == 10
        assert schedule[-1]["book_value"] == 200.0

    def test_schedule_after_partial_depreciation(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000, salvage_value=2000, useful_life_months=60,
        )
        # Depreciate 3 months
        for _ in range(3):
            dm.post_depreciation(asset.id)

        schedule = dm.get_schedule(asset.id)
        # Should have 57 remaining periods
        assert len(schedule) == 57

        # First row should continue from current book value
        monthly = round(10000 / 60, 2)
        accum_after_3 = round(monthly * 3, 2)
        assert schedule[0]["accumulated_depreciation"] == round(accum_after_3 + monthly, 2)


class TestDepreciationFormatting:
    def test_format_asset_list_empty(self):
        text = format_asset_list([])
        assert "No fixed assets" in text

    def test_format_asset_list_with_items(self, ledger):
        dm = DepreciationManager(ledger)
        dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000, salvage_value=2000, useful_life_months=60,
        )
        text = format_asset_list(dm.list_assets())
        assert "Server" in text
        assert "Straight Line" in text
        assert "12,000.00" in text

    def test_format_asset_detail(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server Rack",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000, salvage_value=2000, useful_life_months=60,
            description="Main server rack",
        )
        dm.post_depreciation(asset.id)
        text = format_asset_detail(dm.get(asset.id))
        assert "FIXED ASSET" in text
        assert "Server Rack" in text
        assert "Main server rack" in text
        assert "Depreciation History" in text

    def test_format_schedule(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Server",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=12000, salvage_value=2000, useful_life_months=12,
        )
        schedule = dm.get_schedule(asset.id)
        text = format_depreciation_schedule(schedule, asset.name)
        assert "DEPRECIATION SCHEDULE" in text
        assert "Server" in text
        assert "Period" in text

    def test_format_uop_asset_detail(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Machine",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=50000, salvage_value=5000, useful_life_months=120,
            method="units_of_production",
            total_units=100000,
        )
        text = format_asset_detail(asset)
        assert "Total Units" in text
        assert "100,000" in text


class TestFixedAssetProperties:
    def test_depreciable_base(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Test",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=10000, salvage_value=2000, useful_life_months=60,
        )
        assert asset.depreciable_base == 8000.0

    def test_remaining_life(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Test",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=10000, salvage_value=0, useful_life_months=60,
        )
        assert asset.remaining_life_months == 60
        dm.post_depreciation(asset.id)
        assert asset.remaining_life_months == 59

    def test_is_fully_depreciated(self, ledger):
        dm = DepreciationManager(ledger)
        asset = dm.create_asset(
            name="Test",
            asset_account="equipment",
            accum_dep_account="accum_dep",
            dep_expense_account="dep_expense",
            cost=120, salvage_value=0, useful_life_months=12,
        )
        assert asset.is_fully_depreciated is False
        for _ in range(12):
            dm.post_depreciation(asset.id)
        assert asset.is_fully_depreciated is True
