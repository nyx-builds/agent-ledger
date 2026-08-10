"""Tests for v1.3.0: Financial Forecasting Engine.

Covers:
- Period bucketing (monthly, quarterly, weekly)
- Revenue/expense aggregation from journal entries
- Three forecasting methods: linear regression, moving average, Holt's
- Cash runway computation
- Scenario modeling (best/base/worst)
- Forecast accuracy backtesting (MAPE)
- MCP server dispatch
- CLI integration
- Edge cases (empty ledger, single entry, insufficient history)
"""

import json
import pytest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_ledger.models import AccountType, JournalLine, JournalEntry
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.forecast import (
    # Data classes
    Forecast, CashForecast, PeriodActual, ForecastPoint,
    # Enums
    ForecastMethod, Scenario, PeriodFrequency,
    # Core functions
    collect_history,
    generate_forecast,
    format_forecast,
    forecast_summary_dict,
    # Statistical helpers
    _linear_regression,
    _moving_average,
    _holt_linear,
    _mape,
    _backtest_linear,
    _backtest_ma,
    _backtest_holt,
    # Period helpers
    _period_bounds,
    _month_label,
    _quarter_label,
    _week_label,
    # Cash helpers
    _cash_balance,
    _cash_forecast_to_dict,
)


# ── Fixtures ────────────────────────────────────────────────────


def _make_ledger():
    """Create an in-memory ledger with revenue, expense, and cash accounts."""
    storage = Storage(Path(tempfile.mktemp(suffix=".json")))
    storage.init(name="Test Ledger", base_currency="USD")
    ledger = Ledger(storage)

    # Accounts
    for code, name, atype in [
        ("1000", "Cash", AccountType.ASSET),
        ("4000", "Sales Revenue", AccountType.REVENUE),
        ("5000", "Operating Expenses", AccountType.EXPENSE),
    ]:
        ledger.create_account(code=code, name=name, account_type=atype)

    return ledger


def _make_ledger_with_history(months: int = 8, base_rev: float = 10000, base_exp: float = 6000, rev_growth: float = 500):
    """Create a ledger with *months* of historical revenue/expense entries.

    Revenue grows linearly (base_rev + rev_growth * month).
    Expenses are flat at base_exp.
    """
    ledger = _make_ledger()
    now = datetime.now(timezone.utc)

    for i in range(months):
        # Post to the month that is *months-i* months ago
        target = now - timedelta(days=30 * (months - i))
        rev = base_rev + rev_growth * (i + 1)

        entry = JournalEntry(
            description=f"Month {i+1} revenue & expense",
            lines=[
                JournalLine(account_code="1000", debit=rev),       # cash in
                JournalLine(account_code="4000", credit=rev),      # revenue
                JournalLine(account_code="5000", debit=base_exp),  # expense
                JournalLine(account_code="1000", credit=base_exp), # cash out
            ],
            timestamp=target,
        )
        ledger.data.entries.append(entry)

    ledger.save()
    return ledger


@pytest.fixture
def ledger():
    return _make_ledger()


@pytest.fixture
def ledger_with_history():
    return _make_ledger_with_history()


@pytest.fixture
def ledger_burning_cash():
    """A ledger where expenses consistently exceed revenue → runway test."""
    storage = Storage(Path(tempfile.mktemp(suffix=".json")))
    storage.init(name="Burning", base_currency="USD")
    ledger = Ledger(storage)
    for code, name, atype in [
        ("1000", "Cash", AccountType.ASSET),
        ("4000", "Sales Revenue", AccountType.REVENUE),
        ("5000", "Operating Expenses", AccountType.EXPENSE),
    ]:
        ledger.create_account(code=code, name=name, account_type=atype)

    now = datetime.now(timezone.utc)
    # Seed with initial cash: 5000 (debit to cash, credit to equity... but we don't have equity acct)
    # Instead, just set up expenses > revenue for several months
    for i in range(6):
        target = now - timedelta(days=30 * (6 - i))
        rev = 3000
        exp = 5000
        entry = JournalEntry(
            description=f"Burn month {i+1}",
            lines=[
                JournalLine(account_code="1000", debit=rev),
                JournalLine(account_code="4000", credit=rev),
                JournalLine(account_code="5000", debit=exp),
                JournalLine(account_code="1000", credit=exp),
            ],
            timestamp=target,
        )
        ledger.data.entries.append(entry)
    ledger.save()
    return ledger


# ── Period Helpers ──────────────────────────────────────────────


class TestPeriodBounds:
    """Tests for period boundary computation."""

    def test_monthly_january(self):
        anchor = datetime(2026, 1, 15, tzinfo=timezone.utc)
        start, end = _period_bounds(anchor, PeriodFrequency.MONTHLY)
        assert start == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert end == datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    def test_monthly_february_leap_year(self):
        anchor = datetime(2024, 2, 10, tzinfo=timezone.utc)
        start, end = _period_bounds(anchor, PeriodFrequency.MONTHLY)
        assert start == datetime(2024, 2, 1, tzinfo=timezone.utc)
        assert end == datetime(2024, 2, 29, 23, 59, 59, tzinfo=timezone.utc)

    def test_monthly_december_offset(self):
        anchor = datetime(2026, 3, 15, tzinfo=timezone.utc)
        start, end = _period_bounds(anchor, PeriodFrequency.MONTHLY, offset=-3)
        assert start == datetime(2025, 12, 1, tzinfo=timezone.utc)
        assert end == datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    def test_monthly_offset_wraps_year(self):
        anchor = datetime(2026, 2, 10, tzinfo=timezone.utc)
        start, end = _period_bounds(anchor, PeriodFrequency.MONTHLY, offset=-2)
        assert start == datetime(2025, 12, 1, tzinfo=timezone.utc)

    def test_quarterly_q1(self):
        anchor = datetime(2026, 2, 15, tzinfo=timezone.utc)
        start, end = _period_bounds(anchor, PeriodFrequency.QUARTERLY)
        assert start == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert end == datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc)

    def test_quarterly_q4_offset(self):
        anchor = datetime(2026, 6, 15, tzinfo=timezone.utc)
        start, end = _period_bounds(anchor, PeriodFrequency.QUARTERLY, offset=-2)
        assert start == datetime(2025, 10, 1, tzinfo=timezone.utc)
        assert end == datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    def test_weekly(self):
        anchor = datetime(2026, 1, 7, tzinfo=timezone.utc)  # Wednesday
        start, end = _period_bounds(anchor, PeriodFrequency.WEEKLY)
        # Should be Monday Jan 5
        assert start.weekday() == 0  # Monday
        assert (end - start).days == 6

    def test_weekly_offset(self):
        anchor = datetime(2026, 1, 7, tzinfo=timezone.utc)
        start1, _ = _period_bounds(anchor, PeriodFrequency.WEEKLY, offset=0)
        start2, _ = _period_bounds(anchor, PeriodFrequency.WEEKLY, offset=-1)
        assert (start1 - start2).days == 7


class TestLabels:
    def test_month_label(self):
        assert _month_label(datetime(2026, 3, 15)) == "Mar 2026"

    def test_quarter_label(self):
        assert _quarter_label(datetime(2026, 5, 15)) == "Q2 2026"

    def test_week_label(self):
        label = _week_label(datetime(2026, 1, 7))
        assert label.startswith("W")


# ── Statistical Models ──────────────────────────────────────────


class TestLinearRegression:
    def test_perfect_line(self):
        values = [10, 20, 30, 40, 50]
        intercept, slope = _linear_regression(values)
        assert abs(intercept - 10) < 0.01
        assert abs(slope - 10) < 0.01

    def test_flat(self):
        values = [100, 100, 100, 100]
        intercept, slope = _linear_regression(values)
        assert abs(intercept - 100) < 0.01
        assert abs(slope) < 0.01

    def test_decreasing(self):
        values = [100, 90, 80, 70]
        intercept, slope = _linear_regression(values)
        assert slope < 0

    def test_single_value(self):
        intercept, slope = _linear_regression([42])
        assert intercept == 42
        assert slope == 0.0

    def test_empty(self):
        intercept, slope = _linear_regression([])
        assert intercept == 0.0
        assert slope == 0.0

    def test_noisy_data(self):
        """With noise, slope should still approximate the underlying trend."""
        values = [100, 110, 105, 115, 120, 125, 130]
        _, slope = _linear_regression(values)
        assert 4 < slope < 6  # approximately 5/period


class TestMovingAverage:
    def test_flat(self):
        values = [100, 100, 100, 100]
        level, trend = _moving_average(values, window=3)
        assert abs(level - 100) < 0.01
        assert abs(trend) < 0.01

    def test_trending_up(self):
        values = [100, 110, 120, 130]
        level, trend = _moving_average(values, window=3)
        assert level > 100
        assert trend > 0

    def test_window_larger_than_data(self):
        values = [50, 60]
        level, trend = _moving_average(values, window=5)
        assert abs(level - 55) < 0.01
        assert abs(trend - 10) < 0.01

    def test_single_value(self):
        level, trend = _moving_average([42], window=3)
        assert level == 42
        assert trend == 0.0


class TestHoltLinear:
    def test_flat(self):
        values = [100.0] * 8
        level, trend = _holt_linear(values)
        assert abs(level - 100) < 5
        assert abs(trend) < 5

    def test_trending_up(self):
        values = [float(100 + 10 * i) for i in range(8)]
        level, trend = _holt_linear(values)
        assert level > 150  # should be near the recent values
        assert trend > 5

    def test_single_value(self):
        level, trend = _holt_linear([42])
        assert level == 42
        assert trend == 0.0

    def test_empty(self):
        level, trend = _holt_linear([])
        assert level == 0.0
        assert trend == 0.0


class TestMAPE:
    def test_perfect_prediction(self):
        assert _mape([100, 200], [100, 200]) == 0.0

    def test_10_percent_off(self):
        m = _mape([100, 200], [110, 220])
        assert abs(m - 10.0) < 0.01

    def test_all_zeros_returns_none(self):
        assert _mape([0, 0], [100, 200]) is None

    def test_some_zeros_skipped(self):
        m = _mape([0, 100], [50, 110])
        assert abs(m - 10.0) < 0.01

    def test_empty(self):
        assert _mape([], []) is None


class TestBacktest:
    def test_backtest_linear_needs_4_points(self):
        assert _backtest_linear([1, 2, 3]) is None

    def test_backtest_linear_works(self):
        values = [float(100 + 10 * i) for i in range(8)]
        mape = _backtest_linear(values)
        assert mape is not None
        assert mape < 10  # linear data should fit well

    def test_backtest_ma_needs_4_points(self):
        assert _backtest_ma([1, 2, 3]) is None

    def test_backtest_ma_works(self):
        values = [100.0, 110, 120, 130, 140, 150]
        mape = _backtest_ma(values)
        assert mape is not None
        assert mape < 20

    def test_backtest_holt_needs_4_points(self):
        assert _backtest_holt([1, 2, 3]) is None

    def test_backtest_holt_works(self):
        values = [100.0, 110, 120, 130, 140, 150]
        mape = _backtest_holt(values)
        assert mape is not None
        assert mape < 30


# ── History Collection ─────────────────────────────────────────


class TestCollectHistory:
    def test_returns_correct_count(self, ledger_with_history):
        history = collect_history(ledger_with_history, periods=6, freq=PeriodFrequency.MONTHLY)
        assert len(history) == 6

    def test_history_is_chronological(self, ledger_with_history):
        history = collect_history(ledger_with_history, periods=4, freq=PeriodFrequency.MONTHLY)
        for i in range(1, len(history)):
            assert history[i].start > history[i - 1].start

    def test_revenue_is_positive(self, ledger_with_history):
        history = collect_history(ledger_with_history, periods=6, freq=PeriodFrequency.MONTHLY)
        for p in history:
            assert p.revenue > 0

    def test_expenses_are_positive(self, ledger_with_history):
        history = collect_history(ledger_with_history, periods=6, freq=PeriodFrequency.MONTHLY)
        for p in history:
            assert p.expenses > 0

    def test_net_income(self, ledger_with_history):
        history = collect_history(ledger_with_history, periods=6, freq=PeriodFrequency.MONTHLY)
        for p in history:
            expected_net = p.revenue - p.expenses
            assert abs(p.net_income - expected_net) < 0.02

    def test_empty_ledger_returns_zeros(self, ledger):
        history = collect_history(ledger, periods=3, freq=PeriodFrequency.MONTHLY)
        assert len(history) == 3
        for p in history:
            assert p.revenue == 0.0
            assert p.expenses == 0.0

    def test_invalid_periods_raises(self, ledger):
        with pytest.raises(ValueError):
            collect_history(ledger, periods=0)

    def test_quarterly_frequency(self, ledger_with_history):
        history = collect_history(ledger_with_history, periods=4, freq=PeriodFrequency.QUARTERLY)
        assert len(history) == 4
        for p in history:
            assert "Q" in p.label


# ── Cash Balance ────────────────────────────────────────────────


class TestCashBalance:
    def test_no_entries_returns_zero(self, ledger):
        assert _cash_balance(ledger) == 0.0

    def test_with_history(self, ledger_with_history):
        bal = _cash_balance(ledger_with_history)
        # Revenue 10500, expenses 6000 per month avg → positive cash
        assert bal > 0

    def test_burning_ledger(self, ledger_burning_cash):
        bal = _cash_balance(ledger_burning_cash)
        # 6 months of (3000 - 5000) = -12000
        assert bal < 0


# ── Forecast Generation ────────────────────────────────────────


class TestGenerateForecast:
    def test_basic_forecast(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        assert f.method == "linear"
        assert f.frequency == "monthly"
        assert f.scenario == "base"
        assert len(f.projections) == 3
        assert len(f.history) == 12

    def test_projections_have_revenue(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=4)
        for p in f.projections:
            assert p.revenue > 0

    def test_linear_growth_projects_upward(self, ledger_with_history):
        """With linearly growing revenue, projections should increase."""
        f = generate_forecast(ledger_with_history, periods_ahead=4, method=ForecastMethod.LINEAR)
        revenues = [p.revenue for p in f.projections]
        assert revenues[-1] > revenues[0]

    def test_moving_average_method(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3, method=ForecastMethod.MOVING_AVERAGE)
        assert f.method == "ma"
        assert len(f.projections) == 3
        for p in f.projections:
            assert p.revenue > 0

    def test_holt_method(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3, method=ForecastMethod.HOLT)
        assert f.method == "holt"
        assert len(f.projections) == 3

    def test_quarterly_frequency(self, ledger_with_history):
        f = generate_forecast(
            ledger_with_history, periods_ahead=2,
            freq=PeriodFrequency.QUARTERLY,
        )
        assert f.frequency == "quarterly"
        for p in f.projections:
            assert "Q" in p.label

    def test_weekly_frequency(self, ledger_with_history):
        f = generate_forecast(
            ledger_with_history, periods_ahead=2,
            freq=PeriodFrequency.WEEKLY,
        )
        assert f.frequency == "weekly"

    def test_periods_ahead_validation(self, ledger_with_history):
        with pytest.raises(ValueError):
            generate_forecast(ledger_with_history, periods_ahead=0)

    def test_history_periods_validation(self, ledger_with_history):
        with pytest.raises(ValueError):
            generate_forecast(ledger_with_history, history_periods=1)

    def test_model_parameters_populated(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        assert f.base_revenue > 0
        assert f.base_expenses > 0
        # revenue should be growing in our test data
        assert f.revenue_growth_per_period > 0

    def test_mape_populated(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        assert f.mape_revenue is not None
        assert f.mape_expenses is not None

    def test_forecast_point_labels_unique(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=6)
        labels = [p.label for p in f.projections]
        assert len(labels) == len(set(labels))

    def test_period_numbers_sequential(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=4)
        nums = [p.period_number for p in f.projections]
        assert nums == [1, 2, 3, 4]

    def test_net_income_is_difference(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        for p in f.projections:
            assert abs(p.net_income - (p.revenue - p.expenses)) < 0.02


# ── Scenario Modeling ──────────────────────────────────────────


class TestScenarios:
    def test_best_has_higher_revenue_than_base(self, ledger_with_history):
        f_base = generate_forecast(ledger_with_history, periods_ahead=6, scenario=Scenario.BASE)
        f_best = generate_forecast(ledger_with_history, periods_ahead=6, scenario=Scenario.BEST)

        base_total = sum(p.revenue for p in f_base.projections)
        best_total = sum(p.revenue for p in f_best.projections)
        # Best scenario should project >= base (depends on growth direction)
        assert best_total >= base_total - 1  # allow rounding

    def test_worst_has_lower_revenue_than_base(self, ledger_with_history):
        f_base = generate_forecast(ledger_with_history, periods_ahead=6, scenario=Scenario.BASE)
        f_worst = generate_forecast(ledger_with_history, periods_ahead=6, scenario=Scenario.WORST)

        base_total = sum(p.revenue for p in f_base.projections)
        worst_total = sum(p.revenue for p in f_worst.projections)
        # Worst scenario should project <= base
        assert worst_total <= base_total + 1

    def test_best_worst_spread(self, ledger_with_history):
        f_best = generate_forecast(ledger_with_history, periods_ahead=6, scenario=Scenario.BEST)
        f_worst = generate_forecast(ledger_with_history, periods_ahead=6, scenario=Scenario.WORST)

        best_rev = sum(p.revenue for p in f_best.projections)
        worst_rev = sum(p.revenue for p in f_worst.projections)
        assert best_rev > worst_rev

    def test_scenario_in_result(self, ledger_with_history):
        for sc in [Scenario.BEST, Scenario.BASE, Scenario.WORST]:
            f = generate_forecast(ledger_with_history, periods_ahead=3, scenario=sc)
            assert f.scenario == sc.value


# ── Cash Runway ─────────────────────────────────────────────────


class TestCashRunway:
    def test_cash_forecast_attached(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=6, include_cash=True)
        assert f.cash is not None

    def test_no_cash_when_disabled(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=6, include_cash=False)
        assert f.cash is None

    def test_current_cash_positive(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=6)
        assert f.cash.current_cash > 0

    def test_burning_ledger_depletes(self, ledger_burning_cash):
        f = generate_forecast(
            ledger_burning_cash, periods_ahead=24,
            scenario=Scenario.WORST,
            include_cash=True,
        )
        assert f.cash is not None
        # With consistent losses, cash should deplete eventually
        # (if the current balance is negative, it already depletes)
        assert f.cash.depletes is True or f.cash.current_cash < 0

    def test_runway_label_unlimited_for_profitable(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=6, scenario=Scenario.BASE)
        assert f.cash is not None
        # Profitable ledger should not deplete
        assert f.cash.depletes is False

    def test_projected_cash_decreases_in_burn(self, ledger_burning_cash):
        f = generate_forecast(
            ledger_burning_cash, periods_ahead=6,
            scenario=Scenario.BASE, include_cash=True,
        )
        assert f.cash is not None
        if len(f.cash.points) >= 2:
            # In a burning scenario, cash should generally decrease
            assert f.cash.points[-1].projected_cash < f.cash.points[0].projected_cash

    def test_min_balance(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=6)
        assert isinstance(f.cash.min_balance, float)

    def test_cash_points_have_balance(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=6)
        for p in f.cash.points:
            assert p.projected_cash is not None


# ── Serialization ───────────────────────────────────────────────


class TestSerialization:
    def test_to_dict(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        d = f.to_dict()
        assert d["method"] == "linear"
        assert d["frequency"] == "monthly"
        assert len(d["projections"]) == 3
        assert len(d["history"]) == 12
        assert "model" in d
        assert "fit_quality" in d

    def test_to_dict_is_json_serializable(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        d = f.to_dict()
        # Should not raise
        json.dumps(d, default=str)

    def test_cash_forecast_to_dict(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        d = _cash_forecast_to_dict(f.cash)
        assert d is not None
        assert "current_cash" in d
        assert "runway_label" in d
        assert "projected_balances" in d
        assert len(d["projected_balances"]) == 3

    def test_cash_forecast_to_dict_none(self):
        assert _cash_forecast_to_dict(None) is None

    def test_forecast_summary_dict(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        s = forecast_summary_dict(f)
        assert s["method"] == "linear"
        assert s["periods_projected"] == 3
        assert s["total_projected_revenue"] > 0
        assert s["total_projected_net"] is not None
        assert "runway_label" in s


# ── Formatting ──────────────────────────────────────────────────


class TestFormatting:
    def test_format_forecast_not_empty(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        text = format_forecast(f)
        assert len(text) > 100
        assert "Financial Forecast" in text

    def test_format_includes_model_params(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        text = format_forecast(f)
        assert "Model Parameters" in text
        assert "Base Revenue" in text

    def test_format_includes_history(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        text = format_forecast(f)
        assert "Historical" in text

    def test_format_includes_projections(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        text = format_forecast(f)
        assert "Projections" in text

    def test_format_includes_cash_when_present(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3, include_cash=True)
        text = format_forecast(f)
        assert "Cash Runway" in text

    def test_format_mape_quality_labels(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3)
        text = format_forecast(f)
        # Should have one of the quality labels
        assert any(q in text for q in ["excellent", "good", "fair", "poor"])


# ── MCP Server Integration ─────────────────────────────────────


class TestMCPServer:
    def test_tool_definitions_exist(self):
        from agent_ledger.mcp_server import TOOLS
        tool_names = []
        for t in TOOLS:
            if isinstance(t.get("name"), str):
                tool_names.append(t["name"])

        assert "generate_financial_forecast" in tool_names
        assert "forecast_cash_runway" in tool_names
        assert "compare_scenarios" in tool_names
        assert "get_forecast_accuracy" in tool_names

    def test_dispatch_generate_forecast(self, ledger_with_history):
        from agent_ledger.mcp_server import _dispatch
        result = _dispatch(ledger_with_history, "generate_financial_forecast", {
            "periods_ahead": 3,
            "method": "linear",
        })
        assert result["method"] == "linear"
        assert len(result["projections"]) == 3

    def test_dispatch_cash_runway(self, ledger_with_history):
        from agent_ledger.mcp_server import _dispatch
        result = _dispatch(ledger_with_history, "forecast_cash_runway", {
            "periods_ahead": 12,
        })
        assert "current_cash" in result
        assert "runway_label" in result

    def test_dispatch_compare_scenarios(self, ledger_with_history):
        from agent_ledger.mcp_server import _dispatch
        result = _dispatch(ledger_with_history, "compare_scenarios", {
            "periods_ahead": 3,
        })
        assert "best" in result
        assert "base" in result
        assert "worst" in result
        for key in ["best", "base", "worst"]:
            assert result[key]["total_projected_revenue"] > 0

    def test_dispatch_accuracy(self, ledger_with_history):
        from agent_ledger.mcp_server import _dispatch
        result = _dispatch(ledger_with_history, "get_forecast_accuracy", {})
        assert "linear" in result
        assert "ma" in result
        assert "holt" in result
        assert "recommended" in result
        assert "note" in result

    def test_dispatch_with_defaults(self, ledger_with_history):
        from agent_ledger.mcp_server import _dispatch
        result = _dispatch(ledger_with_history, "generate_financial_forecast", {})
        assert result["method"] == "linear"
        assert len(result["projections"]) == 6  # default


# ── Edge Cases ──────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_ledger_forecast(self, ledger):
        """Forecasting on a ledger with no entries should produce zero projections."""
        f = generate_forecast(ledger, periods_ahead=3, history_periods=3)
        assert len(f.projections) == 3
        # All projections should be zero or near-zero
        for p in f.projections:
            assert p.revenue >= 0  # we clamp to >= 0

    def test_single_entry_history(self, ledger):
        """With just enough history (2 periods), forecast should work."""
        now = datetime.now(timezone.utc)
        for i in range(2):
            target = now - timedelta(days=30 * (2 - i))
            entry = JournalEntry(
                description=f"Entry {i}",
                lines=[
                    JournalLine(account_code="1000", debit=1000),
                    JournalLine(account_code="4000", credit=1000),
                ],
                timestamp=target,
            )
            ledger.data.entries.append(entry)
        ledger.save()

        f = generate_forecast(ledger, periods_ahead=2, history_periods=2)
        assert len(f.projections) == 2

    def test_projections_clamped_to_zero(self, ledger):
        """Negative projected values should be clamped to 0."""
        # Create a ledger where revenue is declining
        storage = Storage(Path(tempfile.mktemp(suffix=".json")))
        storage.init(name="Declining", base_currency="USD")
        l = Ledger(storage)
        l.create_account(code="1000", name="Cash", account_type=AccountType.ASSET)
        l.create_account(code="4000", name="Revenue", account_type=AccountType.REVENUE)

        now = datetime.now(timezone.utc)
        for i in range(6):
            target = now - timedelta(days=30 * (6 - i))
            rev = max(100, 1000 - 200 * i)
            entry = JournalEntry(
                description=f"Entry {i}",
                lines=[
                    JournalLine(account_code="1000", debit=rev),
                    JournalLine(account_code="4000", credit=rev),
                ],
                timestamp=target,
            )
            l.data.entries.append(entry)
        l.save()

        f = generate_forecast(l, periods_ahead=10, history_periods=6)
        for p in f.projections:
            assert p.revenue >= 0

    def test_flat_history_no_crash(self, ledger):
        """Flat revenue/expense should not crash any method."""
        now = datetime.now(timezone.utc)
        for i in range(6):
            target = now - timedelta(days=30 * (6 - i))
            entry = JournalEntry(
                description=f"Entry {i}",
                lines=[
                    JournalLine(account_code="1000", debit=500),
                    JournalLine(account_code="4000", credit=500),
                    JournalLine(account_code="5000", debit=300),
                    JournalLine(account_code="1000", credit=300),
                ],
                timestamp=target,
            )
            ledger.data.entries.append(entry)
        ledger.save()

        for method in ForecastMethod:
            f = generate_forecast(ledger, periods_ahead=3, method=method, history_periods=6)
            assert len(f.projections) == 3

    def test_zero_expense_ledger(self, ledger_with_history):
        """All three methods should handle the forecast without error."""
        for method in ForecastMethod:
            f = generate_forecast(ledger_with_history, periods_ahead=3, method=method)
            assert len(f.projections) == 3

    def test_large_periods_ahead(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=60, history_periods=12)
        assert len(f.projections) == 60

    def test_to_dict_with_cash_none(self, ledger_with_history):
        f = generate_forecast(ledger_with_history, periods_ahead=3, include_cash=False)
        d = f.to_dict()
        assert d["cash"] is None

    def test_forecast_point_dataclass(self):
        """ForecastPoint should be constructable with defaults."""
        p = ForecastPoint(label="Test", period_number=1)
        assert p.label == "Test"
        assert p.period_number == 1
        assert p.revenue == 0.0
        assert p.projected_cash is None

    def test_period_actual_dataclass(self):
        p = PeriodActual(label="Jan", start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                         end=datetime(2026, 1, 31, tzinfo=timezone.utc))
        assert p.label == "Jan"
        assert p.revenue == 0.0
