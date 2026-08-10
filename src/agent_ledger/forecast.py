"""Financial forecasting engine for agent-ledger.

Projects future revenue, expenses, and cash position using historical
trend analysis.  Three forecasting methods are provided:

1. **Linear regression** — fits a least-squares line to historical
   period totals and extrapolates forward.  Best when the trend is
   approximately steady growth or decline.

2. **Moving average** — uses a configurable window of recent periods.
   Conservative: ignores the long-term trend but is robust to
   one-off spikes.

3. **Exponential smoothing (Holt's linear)** — accounts for both
   level and trend, with exponential decay of older observations.
   Good for data with a changing trend.

The engine also computes:
- **Cash runway** — months until projected cash balance hits zero.
- **Scenario modeling** — best / base / worst case via adjustable
  growth-rate adjustments.
- **Forecast accuracy** — backtests the model against the most recent
  actual period and reports mean absolute percentage error (MAPE).

All math is pure Python (stdlib only) — no numpy / scipy needed.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from .models import AccountType
from .ledger import Ledger
from .reports import _ensure_aware, _compute_balances_from_entries


# ── Enums ───────────────────────────────────────────────────────


class ForecastMethod(str, Enum):
    """Available forecasting algorithms."""

    LINEAR = "linear"          # least-squares regression
    MOVING_AVERAGE = "ma"      # simple moving average
    HOLT = "holt"              # Holt's linear exponential smoothing


class Scenario(str, Enum):
    """Pre-built scenario multipliers applied to growth rate."""

    BEST = "best"
    BASE = "base"
    WORST = "worst"


class PeriodFrequency(str, Enum):
    """Aggregation frequency for historical data and projections."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    WEEKLY = "weekly"


# Scenario growth-rate adjustments (relative to the fitted trend).
SCENARIO_ADJUSTMENTS: dict[str, float] = {
    "best": 0.25,    # +25% to the trend's growth rate
    "base": 0.0,
    "worst": -0.35,  # -35% to the trend's growth rate
}


# ── Data Classes ────────────────────────────────────────────────


@dataclass
class PeriodActual:
    """One historical period's actual figures."""

    label: str
    start: datetime
    end: datetime
    revenue: float = 0.0
    expenses: float = 0.0
    net_income: float = 0.0


@dataclass
class ForecastPoint:
    """One projected period in a forecast."""

    label: str
    period_number: int       # 1-based offset from the end of history
    revenue: float = 0.0
    expenses: float = 0.0
    net_income: float = 0.0
    projected_cash: Optional[float] = None  # running cash balance


@dataclass
class CashForecast:
    """Result of a cash-position projection."""

    current_cash: float
    points: list[ForecastPoint] = field(default_factory=list)
    runway_months: Optional[int] = None
    runway_label: Optional[str] = None       # e.g. "8 months" / "unlimited"
    depletes: bool = False                   # does cash hit ≤ 0?
    depletion_month: Optional[str] = None    # label of the month it goes negative
    min_balance: float = 0.0
    scenario: str = "base"


@dataclass
class Forecast:
    """Full forecast result."""

    method: str
    frequency: str
    scenario: str
    history: list[PeriodActual] = field(default_factory=list)
    projections: list[ForecastPoint] = field(default_factory=list)

    # Model parameters
    base_revenue: float = 0.0
    revenue_growth_per_period: float = 0.0
    base_expenses: float = 0.0
    expense_growth_per_period: float = 0.0

    # Fit quality
    mape_revenue: Optional[float] = None
    mape_expenses: Optional[float] = None

    # Optional cash runway
    cash: Optional[CashForecast] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "method": self.method,
            "frequency": self.frequency,
            "scenario": self.scenario,
            "history": [
                {
                    "label": p.label,
                    "revenue": round(p.revenue, 2),
                    "expenses": round(p.expenses, 2),
                    "net_income": round(p.net_income, 2),
                }
                for p in self.history
            ],
            "projections": [
                {
                    "label": p.label,
                    "period_number": p.period_number,
                    "revenue": round(p.revenue, 2),
                    "expenses": round(p.expenses, 2),
                    "net_income": round(p.net_income, 2),
                    "projected_cash": round(p.projected_cash, 2) if p.projected_cash is not None else None,
                }
                for p in self.projections
            ],
            "model": {
                "base_revenue": round(self.base_revenue, 2),
                "revenue_growth_per_period": round(self.revenue_growth_per_period, 2),
                "base_expenses": round(self.base_expenses, 2),
                "expense_growth_per_period": round(self.expense_growth_per_period, 2),
            },
            "fit_quality": {
                "mape_revenue": round(self.mape_revenue, 2) if self.mape_revenue is not None else None,
                "mape_expenses": round(self.mape_expenses, 2) if self.mape_expenses is not None else None,
            },
            "cash": _cash_forecast_to_dict(self.cash) if self.cash else None,
        }


# ── Helpers ─────────────────────────────────────────────────────


def _round2(v: float) -> float:
    return round(v + 1e-9, 2)


def _month_label(dt: datetime) -> str:
    return dt.strftime("%b %Y")


def _quarter_label(dt: datetime) -> str:
    q = (dt.month - 1) // 3 + 1
    return f"Q{q} {dt.year}"


def _week_label(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"W{iso[1]:02d} {iso[0]}"


def _period_bounds(
    anchor: datetime,
    freq: PeriodFrequency,
    offset: int = 0,
) -> tuple[datetime, datetime]:
    """Return (start, end) of the period that contains *anchor* shifted by *offset* periods.

    ``offset=0`` → the period containing *anchor*.
    ``offset=-1`` → the previous period, etc.
    """
    if freq == PeriodFrequency.MONTHLY:
        # Normalise to first-of-month
        m = anchor.month + offset
        y = anchor.year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        # End: last second of the month
        if m == 12:
            end = datetime(y + 1, 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
        else:
            end = datetime(y, m + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
        return start, end

    if freq == PeriodFrequency.QUARTERLY:
        q = (anchor.month - 1) // 3 + 1
        # shift by offset quarters
        total_q = q + offset
        y = anchor.year
        while total_q < 1:
            total_q += 4
            y -= 1
        while total_q > 4:
            total_q -= 4
            y += 1
        start_month = (total_q - 1) * 3 + 1
        start = datetime(y, start_month, 1, tzinfo=timezone.utc)
        end_month = start_month + 2
        end_year = y
        if end_month > 12:
            end_month -= 12
            end_year += 1
        # last day of end_month
        if end_month == 12:
            next_first = datetime(end_year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_first = datetime(end_year, end_month + 1, 1, tzinfo=timezone.utc)
        end = next_first - timedelta(seconds=1)
        return start, end

    # weekly — Monday-start
    iso = anchor.isocalendar()
    monday = datetime.fromisocalendar(iso[0], iso[1], 1)
    monday = monday.replace(tzinfo=timezone.utc)
    start = monday + timedelta(weeks=offset)
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return start, end


def _label_for(start: datetime, freq: PeriodFrequency) -> str:
    if freq == PeriodFrequency.MONTHLY:
        return _month_label(start)
    if freq == PeriodFrequency.QUARTERLY:
        return _quarter_label(start)
    return _week_label(start)


def _net_income_for_period(
    ledger: Ledger,
    start: datetime,
    end: datetime,
) -> tuple[float, float, float]:
    """Return (revenue, expenses, net_income) for the given date range.

    Revenue = sum of credit postings to revenue accounts.
    Expenses = sum of debit postings to expense accounts.
    """
    entries = [
        e for e in ledger.data.entries
        if _ensure_aware(e.timestamp) >= start and _ensure_aware(e.timestamp) <= end
    ]
    accounts = {a.code: a for a in ledger.list_accounts()}
    revenue = 0.0
    expenses = 0.0
    for entry in entries:
        for line in entry.lines:
            acct = accounts.get(line.account_code)
            if acct is None:
                continue
            if acct.account_type == AccountType.REVENUE:
                revenue += line.credit - line.debit
            elif acct.account_type == AccountType.EXPENSE:
                expenses += line.debit - line.credit
    net = revenue - expenses
    return _round2(revenue), _round2(expenses), _round2(net)


def _cash_balance(ledger: Ledger, as_of: Optional[datetime] = None) -> float:
    """Compute the total cash balance (sum of active asset accounts tagged 'cash' or containing 'cash' in name).

    Falls back to all asset accounts if none are tagged.
    """
    accounts = ledger.list_accounts()
    cash_accounts = [
        a for a in accounts
        if a.account_type == AccountType.ASSET and a.active
        and ("cash" in a.name.lower() or "cash" in a.tags or "bank" in a.name.lower())
    ]
    if not cash_accounts:
        cash_accounts = [a for a in accounts if a.account_type == AccountType.ASSET and a.active]

    entries = ledger.data.entries
    if as_of is not None:
        as_of = _ensure_aware(as_of)
        entries = [e for e in entries if _ensure_aware(e.timestamp) <= as_of]

    total = 0.0
    cash_codes = {a.code for a in cash_accounts}
    for entry in entries:
        for line in entry.lines:
            if line.account_code in cash_codes:
                total += line.debit - line.credit
    return _round2(total)


# ── Statistical models (pure Python) ────────────────────────────


def _linear_regression(values: list[float]) -> tuple[float, float]:
    """Least-squares regression: returns (intercept, slope).

    ``y = intercept + slope * x`` where x = 0, 1, 2, ...
    """
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return values[0], 0.0
    xs = list(range(n))
    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(values)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return y_mean, 0.0
    slope = num / den
    intercept = y_mean - slope * x_mean
    return intercept, slope


def _moving_average(values: list[float], window: int = 3) -> tuple[float, float]:
    """Return (level, trend) using moving average.

    ``level`` = average of the last *window* values.
    ``trend`` = average change between consecutive periods in the window.
    """
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    w = min(window, n)
    recent = values[-w:]
    level = statistics.mean(recent)
    if w < 2:
        return level, 0.0
    diffs = [recent[i] - recent[i - 1] for i in range(1, w)]
    trend = statistics.mean(diffs)
    return level, trend


def _holt_linear(
    values: list[float],
    alpha: float = 0.5,
    beta: float = 0.3,
) -> tuple[float, float]:
    """Holt's linear trend exponential smoothing.

    Returns (level, trend) for the *next* period.
    """
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return values[0], 0.0
    # initialise
    level = values[0]
    trend = values[1] - values[0]
    for i in range(1, n):
        prev_level = level
        level = alpha * values[i] + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
    return level, trend


def _mape(actuals: list[float], predicted: list[float]) -> Optional[float]:
    """Mean Absolute Percentage Error.  Returns None if all actuals are zero."""
    errors = []
    for a, p in zip(actuals, predicted):
        if abs(a) < 1e-9:
            continue
        errors.append(abs((a - p) / a) * 100)
    if not errors:
        return None
    return statistics.mean(errors)


def _backtest_linear(values: list[float]) -> Optional[float]:
    """Backtest the linear model: predict each point using data up to that point.

    Returns MAPE over points where data before them exists.
    """
    if len(values) < 4:
        return None
    actuals: list[float] = []
    predicted: list[float] = []
    for i in range(3, len(values)):
        history = values[:i]
        intercept, slope = _linear_regression(history)
        pred = intercept + slope * i
        actuals.append(values[i])
        predicted.append(pred)
    return _mape(actuals, predicted)


def _backtest_ma(values: list[float], window: int = 3) -> Optional[float]:
    if len(values) < 4:
        return None
    actuals: list[float] = []
    predicted: list[float] = []
    for i in range(3, len(values)):
        history = values[:i]
        level, trend = _moving_average(history, window)
        pred = level + trend  # one step ahead
        actuals.append(values[i])
        predicted.append(pred)
    return _mape(actuals, predicted)


def _backtest_holt(values: list[float]) -> Optional[float]:
    if len(values) < 4:
        return None
    actuals: list[float] = []
    predicted: list[float] = []
    for i in range(3, len(values)):
        history = values[:i]
        level, trend = _holt_linear(history)
        pred = level + trend
        actuals.append(values[i])
        predicted.append(pred)
    return _mape(actuals, predicted)


# ── Core API ────────────────────────────────────────────────────


def collect_history(
    ledger: Ledger,
    periods: int = 12,
    freq: PeriodFrequency = PeriodFrequency.MONTHLY,
    end_date: Optional[datetime] = None,
) -> list[PeriodActual]:
    """Collect historical period-by-period actuals from the ledger.

    Args:
        ledger: The ledger instance.
        periods: Number of past periods to collect.
        freq: Aggregation frequency.
        end_date: Anchor date (defaults to now).

    Returns:
        List of ``PeriodActual`` objects in chronological order.
    """
    if periods < 1:
        raise ValueError("periods must be >= 1")
    if end_date is None:
        end_date = datetime.now(timezone.utc)
    end_date = _ensure_aware(end_date)

    result: list[PeriodActual] = []
    for i in range(periods, 0, -1):
        offset = -i  # i periods ago
        start, end = _period_bounds(end_date, freq, offset)
        rev, exp, net = _net_income_for_period(ledger, start, end)
        result.append(PeriodActual(
            label=_label_for(start, freq),
            start=start,
            end=end,
            revenue=rev,
            expenses=exp,
            net_income=net,
        ))
    return result


def generate_forecast(
    ledger: Ledger,
    periods_ahead: int = 6,
    method: ForecastMethod = ForecastMethod.LINEAR,
    freq: PeriodFrequency = PeriodFrequency.MONTHLY,
    history_periods: int = 12,
    scenario: Scenario = Scenario.BASE,
    include_cash: bool = True,
    end_date: Optional[datetime] = None,
) -> Forecast:
    """Generate a full financial forecast.

    Args:
        ledger: The ledger instance.
        periods_ahead: How many future periods to project.
        method: Forecasting algorithm (linear, ma, holt).
        freq: Aggregation frequency (monthly, quarterly, weekly).
        history_periods: Number of past periods to use for fitting.
        scenario: Scenario multiplier (best/base/worst).
        include_cash: Whether to compute cash runway.
        end_date: Anchor date (defaults to now).

    Returns:
        A ``Forecast`` object with history, projections, model params,
        fit quality (MAPE), and optional cash runway.
    """
    if periods_ahead < 1:
        raise ValueError("periods_ahead must be >= 1")
    if history_periods < 2:
        raise ValueError("history_periods must be >= 2 to fit a trend")

    # 1. Collect history
    history = collect_history(ledger, history_periods, freq, end_date)

    rev_values = [p.revenue for p in history]
    exp_values = [p.expenses for p in history]

    # 2. Fit the model
    adj = SCENARIO_ADJUSTMENTS[scenario.value]

    if method == ForecastMethod.LINEAR:
        rev_intercept, rev_slope = _linear_regression(rev_values)
        exp_intercept, exp_slope = _linear_regression(exp_values)
        rev_base = rev_intercept + rev_slope * (len(rev_values) - 1)
        exp_base = exp_intercept + exp_slope * (len(exp_values) - 1)
        rev_growth = rev_slope * (1 + adj)
        exp_growth = exp_slope * (1 + adj)
        mape_rev = _backtest_linear(rev_values)
        mape_exp = _backtest_linear(exp_values)

    elif method == ForecastMethod.MOVING_AVERAGE:
        window = min(3, len(rev_values))
        rev_level, rev_trend = _moving_average(rev_values, window)
        exp_level, exp_trend = _moving_average(exp_values, window)
        rev_base = rev_level
        exp_base = exp_level
        rev_growth = rev_trend * (1 + adj)
        exp_growth = exp_trend * (1 + adj)
        mape_rev = _backtest_ma(rev_values, window)
        mape_exp = _backtest_ma(exp_values, window)

    else:  # Holt
        rev_level, rev_trend = _holt_linear(rev_values)
        exp_level, exp_trend = _holt_linear(exp_values)
        rev_base = rev_level
        exp_base = exp_level
        rev_growth = rev_trend * (1 + adj)
        exp_growth = exp_trend * (1 + adj)
        mape_rev = _backtest_holt(rev_values)
        mape_exp = _backtest_holt(exp_values)

    # 3. Generate projections
    anchor = end_date or datetime.now(timezone.utc)
    anchor = _ensure_aware(anchor)
    projections: list[ForecastPoint] = []

    for step in range(1, periods_ahead + 1):
        projected_rev = max(0.0, rev_base + rev_growth * step)
        projected_exp = max(0.0, exp_base + exp_growth * step)
        projected_net = projected_rev - projected_exp

        start, end = _period_bounds(anchor, freq, step)
        label = _label_for(start, freq)

        projections.append(ForecastPoint(
            label=label,
            period_number=step,
            revenue=_round2(projected_rev),
            expenses=_round2(projected_exp),
            net_income=_round2(projected_net),
        ))

    forecast = Forecast(
        method=method.value,
        frequency=freq.value,
        scenario=scenario.value,
        history=history,
        projections=projections,
        base_revenue=_round2(rev_base),
        revenue_growth_per_period=_round2(rev_growth),
        base_expenses=_round2(exp_base),
        expense_growth_per_period=_round2(exp_growth),
        mape_revenue=mape_rev,
        mape_expenses=mape_exp,
    )

    # 4. Cash runway
    if include_cash:
        forecast.cash = _compute_cash_runway(
            ledger, projections, freq, anchor, scenario.value,
        )

    return forecast


def _compute_cash_runway(
    ledger: Ledger,
    projections: list[ForecastPoint],
    freq: PeriodFrequency,
    anchor: datetime,
    scenario: str,
) -> CashForecast:
    """Project the running cash balance forward and compute runway."""
    current = _cash_balance(ledger, as_of=anchor)

    running = current
    points: list[ForecastPoint] = []
    depletes = False
    depletion_label: Optional[str] = None
    min_balance = current

    for proj in projections:
        running += proj.net_income
        running = _round2(running)
        proj.projected_cash = running
        points.append(proj)

        if running < min_balance:
            min_balance = running

        if running <= 0 and not depletes:
            depletes = True
            depletion_label = proj.label

    # Runway estimate
    runway_months: Optional[int] = None
    runway_label: Optional[str] = None

    if depletes:
        # Count periods before depletion
        periods_to_zero = 0
        for p in points:
            periods_to_zero += 1
            if p.projected_cash is not None and p.projected_cash <= 0:
                break
        if freq == PeriodFrequency.MONTHLY:
            runway_months = periods_to_zero
            runway_label = f"{periods_to_zero} months"
        elif freq == PeriodFrequency.QUARTERLY:
            runway_months = periods_to_zero * 3
            runway_label = f"{periods_to_zero} quarters (~{runway_months} months)"
        else:
            runway_months = max(1, round(periods_to_zero / 4.33))
            runway_label = f"{periods_to_zero} weeks (~{runway_months} months)"
    else:
        runway_label = "unlimited"
        runway_months = None

    return CashForecast(
        current_cash=_round2(current),
        points=points,
        runway_months=runway_months,
        runway_label=runway_label,
        depletes=depletes,
        depletion_month=depletion_label,
        min_balance=_round2(min_balance),
        scenario=scenario,
    )


def _cash_forecast_to_dict(cash: Optional[CashForecast]) -> Optional[dict]:
    if cash is None:
        return None
    return {
        "current_cash": round(cash.current_cash, 2),
        "runway_months": cash.runway_months,
        "runway_label": cash.runway_label,
        "depletes": cash.depletes,
        "depletion_month": cash.depletion_month,
        "min_balance": round(cash.min_balance, 2),
        "scenario": cash.scenario,
        "projected_balances": [
            {"label": p.label, "balance": round(p.projected_cash, 2) if p.projected_cash is not None else None}
            for p in cash.points
        ],
    }


# ── Formatting ──────────────────────────────────────────────────


def format_forecast(forecast: Forecast) -> str:
    """Format a forecast as a readable text report."""
    lines: list[str] = []
    lines.append(f"╔═══ Financial Forecast — {forecast.method.upper()} · {forecast.frequency} · {forecast.scenario.upper()} ═══╗")
    lines.append("")

    # Model summary
    lines.append("Model Parameters:")
    lines.append(f"  Base Revenue:    {forecast.base_revenue:>14,.2f} / period")
    lines.append(f"  Rev Growth:      {forecast.revenue_growth_per_period:>+14,.2f} / period")
    lines.append(f"  Base Expenses:   {forecast.base_expenses:>14,.2f} / period")
    lines.append(f"  Exp Growth:      {forecast.expense_growth_per_period:>+14,.2f} / period")
    lines.append("")

    # Fit quality
    if forecast.mape_revenue is not None or forecast.mape_expenses is not None:
        lines.append("Fit Quality (backtested MAPE):")
        if forecast.mape_revenue is not None:
            quality = _mape_label(forecast.mape_revenue)
            lines.append(f"  Revenue MAPE:    {forecast.mape_revenue:>10.1f}%  ({quality})")
        if forecast.mape_expenses is not None:
            quality = _mape_label(forecast.mape_expenses)
            lines.append(f"  Expenses MAPE:   {forecast.mape_expenses:>10.1f}%  ({quality})")
        lines.append("")

    # History
    if forecast.history:
        lines.append("Historical Periods:")
        lines.append(f"  {'Period':<16} {'Revenue':>12} {'Expenses':>12} {'Net':>12}")
        lines.append(f"  {'─' * 16} {'─' * 12} {'─' * 12} {'─' * 12}")
        for p in forecast.history:
            lines.append(f"  {p.label:<16} {p.revenue:>12,.2f} {p.expenses:>12,.2f} {p.net_income:>+12,.2f}")
        lines.append("")

    # Projections
    lines.append("Projections:")
    lines.append(f"  {'Period':<16} {'Revenue':>12} {'Expenses':>12} {'Net':>12} {'Cash':>14}")
    lines.append(f"  {'─' * 16} {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 14}")
    for p in forecast.projections:
        cash_str = f"{p.projected_cash:>,.2f}" if p.projected_cash is not None else "—"
        lines.append(f"  {p.label:<16} {p.revenue:>12,.2f} {p.expenses:>12,.2f} {p.net_income:>+12,.2f} {cash_str:>14}")
    lines.append("")

    # Cash runway
    if forecast.cash:
        c = forecast.cash
        lines.append("Cash Runway Analysis:")
        lines.append(f"  Current Cash:    {c.current_cash:>14,.2f}")
        lines.append(f"  Runway:          {c.runway_label:>>14}")
        depl = f"Yes — {c.depletion_month}" if c.depletes and c.depletion_month else ("Yes" if c.depletes else "No")
        lines.append(f"  Depletes:        {depl}")
        lines.append(f"  Min Balance:     {c.min_balance:>14,.2f}")
        lines.append("")

    return "\n".join(lines)


def _mape_label(mape: float) -> str:
    """Human-readable accuracy label based on MAPE."""
    if mape < 10:
        return "excellent"
    if mape < 20:
        return "good"
    if mape < 50:
        return "fair"
    return "poor"


def forecast_summary_dict(forecast: Forecast) -> dict[str, Any]:
    """A compact summary dict (no per-period detail) for list views."""
    total_proj_rev = sum(p.revenue for p in forecast.projections)
    total_proj_exp = sum(p.expenses for p in forecast.projections)
    avg_net = statistics.mean(p.net_income for p in forecast.projections) if forecast.projections else 0.0
    return {
        "method": forecast.method,
        "frequency": forecast.frequency,
        "scenario": forecast.scenario,
        "periods_projected": len(forecast.projections),
        "total_projected_revenue": round(total_proj_rev, 2),
        "total_projected_expenses": round(total_proj_exp, 2),
        "total_projected_net": round(total_proj_rev - total_proj_exp, 2),
        "avg_net_per_period": round(avg_net, 2),
        "base_revenue": forecast.base_revenue,
        "revenue_growth_per_period": forecast.revenue_growth_per_period,
        "base_expenses": forecast.base_expenses,
        "expense_growth_per_period": forecast.expense_growth_per_period,
        "mape_revenue": round(forecast.mape_revenue, 2) if forecast.mape_revenue is not None else None,
        "mape_expenses": round(forecast.mape_expenses, 2) if forecast.mape_expenses is not None else None,
        "runway_label": forecast.cash.runway_label if forecast.cash else None,
        "runway_months": forecast.cash.runway_months if forecast.cash else None,
        "depletes": forecast.cash.depletes if forecast.cash else None,
    }
