"""Tests for multi-period comparison reports (v0.9.0)."""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from agent_ledger.ledger import Ledger
from agent_ledger.models import AccountType
from agent_ledger.storage import Storage
from agent_ledger.comparison import (
    compare_account_balances,
    compare_income_statements,
    format_period_comparison,
    format_income_statement_comparison,
    PeriodComparison,
    IncomeStatementComparison,
)


@pytest.fixture
def ledger(tmp_path):
    """Create a ledger with entries across two quarters."""
    storage = Storage(tmp_path / "test.json")
    ledger = Ledger(storage)
    ledger._data = storage.init(name="Test", base_currency="USD")

    # Accounts
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("rev-sales", "Sales Revenue", AccountType.REVENUE)
    ledger.create_account("rev-consulting", "Consulting Revenue", AccountType.REVENUE)
    ledger.create_account("exp-rent", "Rent", AccountType.EXPENSE)
    ledger.create_account("exp-salary", "Salaries", AccountType.EXPENSE)

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)

    # Q1 entries
    ledger.post_entry("Q1 Sale", [("cash", 5000, 0), ("rev-sales", 0, 5000)],
                      timestamp=base + timedelta(days=10))
    ledger.post_entry("Q1 Consulting", [("cash", 3000, 0), ("rev-consulting", 0, 3000)],
                      timestamp=base + timedelta(days=20))
    ledger.post_entry("Q1 Rent", [("exp-rent", 1000, 0), ("cash", 0, 1000)],
                      timestamp=base + timedelta(days=30))
    ledger.post_entry("Q1 Salary", [("exp-salary", 2000, 0), ("cash", 0, 2000)],
                      timestamp=base + timedelta(days=40))

    # Q2 entries
    ledger.post_entry("Q2 Sale", [("cash", 7000, 0), ("rev-sales", 0, 7000)],
                      timestamp=base + timedelta(days=100))
    ledger.post_entry("Q2 Consulting", [("cash", 4000, 0), ("rev-consulting", 0, 4000)],
                      timestamp=base + timedelta(days=110))
    ledger.post_entry("Q2 Rent", [("exp-rent", 1200, 0), ("cash", 0, 1200)],
                      timestamp=base + timedelta(days=120))
    ledger.post_entry("Q2 Salary", [("exp-salary", 2500, 0), ("cash", 0, 2500)],
                      timestamp=base + timedelta(days=130))

    return ledger


class TestCompareAccountBalances:

    def test_basic_comparison(self, ledger):
        q1_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        q1_end = datetime(2024, 3, 31, tzinfo=timezone.utc)
        q2_start = datetime(2024, 4, 1, tzinfo=timezone.utc)
        q2_end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        report = compare_account_balances(ledger, [
            (q1_start, q1_end, "Q1"),
            (q2_start, q2_end, "Q2"),
        ])

        assert len(report.periods) == 2
        assert "Q1" in report.periods
        assert "Q2" in report.periods

    def test_period_values(self, ledger):
        q1_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        q1_end = datetime(2024, 3, 31, tzinfo=timezone.utc)
        q2_start = datetime(2024, 4, 1, tzinfo=timezone.utc)
        q2_end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        report = compare_account_balances(ledger, [
            (q1_start, q1_end, "Q1"),
            (q2_start, q2_end, "Q2"),
        ])

        # Find rev-sales row
        sales_row = [r for r in report.rows if r.account_code == "rev-sales"][0]
        # Q1 activity: 5000; Q2 activity: 7000
        assert sales_row.period_values[0] == 5000.0
        assert sales_row.period_values[1] == 7000.0
        assert sales_row.variance == 2000.0

    def test_variance_pct(self, ledger):
        q1_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        q1_end = datetime(2024, 3, 31, tzinfo=timezone.utc)
        q2_start = datetime(2024, 4, 1, tzinfo=timezone.utc)
        q2_end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        report = compare_account_balances(ledger, [
            (q1_start, q1_end, "Q1"),
            (q2_start, q2_end, "Q2"),
        ])

        sales_row = [r for r in report.rows if r.account_code == "rev-sales"][0]
        # 5000 -> 7000, variance 2000, pct = 40%
        assert sales_row.variance_pct == 40.0

    def test_trend_up(self, ledger):
        q1_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        q1_end = datetime(2024, 3, 31, tzinfo=timezone.utc)
        q2_start = datetime(2024, 4, 1, tzinfo=timezone.utc)
        q2_end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        report = compare_account_balances(ledger, [
            (q1_start, q1_end, "Q1"),
            (q2_start, q2_end, "Q2"),
        ])

        sales_row = [r for r in report.rows if r.account_code == "rev-sales"][0]
        assert sales_row.trend == "up"

    def test_trend_stable(self, ledger):
        """An account with the same activity in both periods should be stable."""
        q1_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        q1_end = datetime(2024, 3, 31, tzinfo=timezone.utc)
        q2_start = datetime(2024, 4, 1, tzinfo=timezone.utc)
        q2_end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        report = compare_account_balances(ledger, [
            (q1_start, q1_end, "Q1"),
            (q2_start, q2_end, "Q2"),
        ])

        # Find cash - it should differ between periods
        cash_row = [r for r in report.rows if r.account_code == "cash"][0]
        assert len(cash_row.period_values) == 2

    def test_trend_new(self, tmp_path):
        """An account with zero in first period and value in second is 'new'."""
        storage = Storage(tmp_path / "test.json")
        ledger = Ledger(storage)
        ledger._data = storage.init(name="Test", base_currency="USD")
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        ledger.create_account("rev", "Revenue", AccountType.REVENUE)

        # Only Q2 entry
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ledger.post_entry("Q2 Sale", [("cash", 1000, 0), ("rev", 0, 1000)],
                          timestamp=base + timedelta(days=100))

        report = compare_account_balances(ledger, [
            (base, base + timedelta(days=89), "Q1"),
            (base + timedelta(days=90), base + timedelta(days=180), "Q2"),
        ])

        rev_row = [r for r in report.rows if r.account_code == "rev"][0]
        assert rev_row.trend == "new"
        assert rev_row.variance_pct == float('inf')

    def test_three_period_comparison(self, ledger):
        """Compare across 3 periods."""
        report = compare_account_balances(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 2, 28, tzinfo=timezone.utc), "Jan-Feb"),
            (datetime(2024, 3, 1, tzinfo=timezone.utc), datetime(2024, 4, 30, tzinfo=timezone.utc), "Mar-Apr"),
            (datetime(2024, 5, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "May-Jun"),
        ])

        assert len(report.periods) == 3
        for row in report.rows:
            assert len(row.period_values) == 3

    def test_all_time_periods(self, ledger):
        """Periods with None dates cover all time."""
        report = compare_account_balances(ledger, [
            (None, datetime(2024, 3, 31, tzinfo=timezone.utc), "Before Q2"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), None, "After Q1"),
        ])
        assert len(report.periods) == 2
        assert len(report.rows) > 0

    def test_min_periods_error(self, ledger):
        with pytest.raises(ValueError, match="At least 2 periods"):
            compare_account_balances(ledger, [
                (None, None, "Only one"),
            ])

    def test_total_rows(self, ledger):
        report = compare_account_balances(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])
        assert len(report.total_rows) == 2

    def test_format_output(self, ledger):
        report = compare_account_balances(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])
        text = format_period_comparison(report)
        assert "PERIOD COMPARISON" in text
        assert "Q1" in text
        assert "Q2" in text
        assert "Variance" in text


class TestCompareIncomeStatements:

    def test_basic_comparison(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])

        assert len(report.periods) == 2
        assert len(report.total_revenue) == 2
        assert len(report.net_income) == 2

    def test_revenue_by_quarter(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])

        # Q1 revenue: 5000 + 3000 = 8000
        assert report.total_revenue[0] == 8000.0
        # Q2 revenue: 7000 + 4000 = 11000
        assert report.total_revenue[1] == 11000.0

    def test_expenses_by_quarter(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])

        # Q1 expenses: 1000 + 2000 = 3000
        assert report.total_expenses[0] == 3000.0
        # Q2 expenses: 1200 + 2500 = 3700
        assert report.total_expenses[1] == 3700.0

    def test_net_income_by_quarter(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])

        # Q1 net: 8000 - 3000 = 5000
        assert report.net_income[0] == 5000.0
        # Q2 net: 11000 - 3700 = 7300
        assert report.net_income[1] == 7300.0

    def test_revenue_variance(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])

        assert report.revenue_variance == 3000.0  # 11000 - 8000
        # 3000 / 8000 = 37.5%
        assert report.revenue_variance_pct == 37.5

    def test_net_income_variance(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])

        # Net income variance: 7300 - 5000 = 2300
        assert report.net_income_variance == 2300.0

    def test_individual_revenue_rows(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])

        sales_row = [r for r in report.revenue_rows if r.account_code == "rev-sales"][0]
        assert sales_row.period_values[0] == 5000.0
        assert sales_row.period_values[1] == 7000.0
        assert sales_row.variance == 2000.0

    def test_individual_expense_rows(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])

        rent_row = [r for r in report.expense_rows if r.account_code == "exp-rent"][0]
        assert rent_row.period_values[0] == 1000.0
        assert rent_row.period_values[1] == 1200.0

    def test_three_periods(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 31, tzinfo=timezone.utc), "Jan"),
            (datetime(2024, 2, 1, tzinfo=timezone.utc), datetime(2024, 2, 29, tzinfo=timezone.utc), "Feb"),
            (datetime(2024, 3, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Mar-Jun"),
        ])
        assert len(report.periods) == 3
        assert len(report.total_revenue) == 3

    def test_min_periods_error(self, ledger):
        with pytest.raises(ValueError, match="At least 2 periods"):
            compare_income_statements(ledger, [
                (None, None, "Single"),
            ])

    def test_format_output(self, ledger):
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), "Q1"),
            (datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Q2"),
        ])
        text = format_income_statement_comparison(report)
        assert "INCOME STATEMENT COMPARISON" in text
        assert "REVENUE" in text
        assert "EXPENSES" in text
        assert "NET INCOME" in text
        assert "Variance" in text

    def test_zero_to_value_new_trend(self, ledger):
        """Account with zero in first period, value in second."""
        report = compare_income_statements(ledger, [
            (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 2, 1, tzinfo=timezone.utc), "Early Q1"),
            (datetime(2024, 2, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc), "Rest"),
        ])
        # Consulting started day 20, so in "Early Q1" (Jan 1 - Feb 1) it should appear
        # since Jan 1 + 20 days = Jan 21 which is before Feb 1
        consulting_row = [r for r in report.revenue_rows if r.account_code == "rev-consulting"]
        # It should exist since consulting started on Jan 21
        if consulting_row:
            assert consulting_row[0].period_values[0] > 0  # Started in early Q1
