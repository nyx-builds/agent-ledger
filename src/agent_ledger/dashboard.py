"""HTML Dashboard for agent-ledger — self-contained financial dashboard.

Generates a complete, standalone HTML file with embedded CSS and JavaScript
that visualizes the ledger's financial state: balance sheet, income statement,
account balances, trial balance, and key ratios. No external dependencies,
no server needed — just open the file in a browser.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Optional

from .ledger import Ledger
from .reports import (
    generate_trial_balance, generate_income_statement, generate_balance_sheet,
)
from .ratios import compute_ratios, get_financial_health
from .alerts import AlertManager


def _esc(text: str) -> str:
    """HTML-escape text."""
    return html.escape(str(text))


def _fmt_money(amount: float) -> str:
    """Format a monetary amount for display."""
    if amount < 0:
        return f"<span class='neg'>(${abs(amount):,.2f})</span>"
    return f"${amount:,.2f}"


def generate_dashboard_html(
    ledger: Ledger,
    title: str = "Agent Ledger Dashboard",
    include_alerts: bool = True,
) -> str:
    """Generate a complete standalone HTML dashboard.

    Args:
        ledger: The ledger to generate a dashboard for
        title: HTML page title
        include_alerts: Whether to include the alerts section

    Returns:
        Complete HTML document as a string
    """
    now = datetime.now(timezone.utc)

    # Generate all reports
    trial_balance = generate_trial_balance(ledger)
    income_stmt = generate_income_statement(ledger)
    balance_sheet = generate_balance_sheet(ledger)
    ratios = compute_ratios(ledger)
    health_dict = get_financial_health(ratios)
    # Derive an overall health string from the sub-statuses
    statuses = [v.get("status", "unknown") for v in health_dict.values()]
    if not statuses:
        health = "unknown"
    elif any(s == "at_risk" for s in statuses):
        health = "critical"
    elif any(s == "marginal" for s in statuses):
        health = "warning"
    elif all(s == "healthy" for s in statuses):
        health = "excellent"
    else:
        health = "fair"

    # Accounts summary
    accounts = ledger.list_accounts()
    total_accounts = len(accounts)
    active_accounts = len([a for a in accounts if a.active])

    # Entries summary
    total_entries = len(ledger.data.entries)
    reconciled_entries = len([e for e in ledger.data.entries if e.reconciled])

    # Alerts
    alert_triggers = []
    if include_alerts:
        try:
            am = AlertManager(ledger)
            alert_triggers = am.list_triggers(acknowledged=False, limit=10)
        except Exception:
            pass

    # Health color
    health_color = {
        "excellent": "#27ae60",
        "good": "#2ecc71",
        "fair": "#f39c12",
        "warning": "#e67e22",
        "critical": "#e74c3c",
        "unknown": "#95a5a6",
    }.get(health, "#95a5a6")

    # ── Build HTML ──────────────────────────────────────────────────

    parts: list[str] = []

    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    background: #0f1117;
    color: #e1e4e8;
    line-height: 1.6;
    padding: 20px;
}}
.container {{ max-width: 1200px; margin: 0 auto; }}
header {{
    background: linear-gradient(135deg, #1a1d28 0%, #2d3142 100%);
    border-radius: 12px;
    padding: 24px 32px;
    margin-bottom: 24px;
    border: 1px solid #2d3142;
}}
header h1 {{ font-size: 24px; color: #fff; margin-bottom: 4px; }}
header .subtitle {{ color: #8b949e; font-size: 14px; }}
.grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}}
.card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 20px;
}}
.card .label {{ font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
.card .value {{ font-size: 28px; font-weight: 700; color: #fff; }}
.card .value.positive {{ color: #3fb950; }}
.card .value.negative {{ color: #f85149; }}
.section {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 24px;
    margin-bottom: 24px;
    overflow-x: auto;
}}
.section h2 {{ font-size: 18px; color: #fff; margin-bottom: 16px; border-bottom: 1px solid #30363d; padding-bottom: 8px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th {{ text-align: left; color: #8b949e; font-weight: 600; padding: 10px 12px; border-bottom: 1px solid #30363d; white-space: nowrap; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; }}
td.num {{ text-align: right; font-family: 'SF Mono', 'Cascadia Code', monospace; white-space: nowrap; }}
.neg {{ color: #f85149; }}
.pos {{ color: #3fb950; }}
.badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
}}
.health-badge {{ background: {health_color}; color: #fff; }}
.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
@media (max-width: 768px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
.alert-item {{
    background: #21262d;
    border-left: 3px solid #f85149;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 8px;
    font-size: 13px;
}}
.alert-item.warning {{ border-left-color: #d29922; }}
.alert-item.info {{ border-left-color: #58a6ff; }}
.alert-item .severity {{ font-weight: 700; text-transform: uppercase; font-size: 11px; }}
.empty-state {{ text-align: center; padding: 32px; color: #8b949e; }}
.mono {{ font-family: 'SF Mono', 'Cascadia Code', monospace; }}
.footer {{ text-align: center; color: #484f58; font-size: 12px; padding: 16px 0; }}
</style>
</head>
<body>
<div class="container">
<header>
    <h1>📊 {_esc(title)}</h1>
    <div class="subtitle">
        {_esc(ledger.data.name)} · Base Currency: {_esc(ledger.data.base_currency)} ·
        Generated: {now.strftime("%Y-%m-%d %H:%M UTC")}
    </div>
</header>
""")

    # ── KPI Cards ───────────────────────────────────────────────────

    parts.append('<div class="grid">')

    parts.append(f"""
    <div class="card">
        <div class="label">Total Assets</div>
        <div class="value {'positive' if balance_sheet.total_assets >= 0 else 'negative'}">
            {_fmt_money(balance_sheet.total_assets)}
        </div>
    </div>
    """)

    parts.append(f"""
    <div class="card">
        <div class="label">Total Liabilities</div>
        <div class="value {'negative' if balance_sheet.total_liabilities > 0 else ''}">
            {_fmt_money(balance_sheet.total_liabilities)}
        </div>
    </div>
    """)

    parts.append(f"""
    <div class="card">
        <div class="label">Total Equity</div>
        <div class="value {'positive' if balance_sheet.total_equity >= 0 else 'negative'}">
            {_fmt_money(balance_sheet.total_equity)}
        </div>
    </div>
    """)

    parts.append(f"""
    <div class="card">
        <div class="label">Net Income</div>
        <div class="value {'positive' if income_stmt.net_income >= 0 else 'negative'}">
            {_fmt_money(income_stmt.net_income)}
        </div>
    </div>
    """)

    parts.append(f"""
    <div class="card">
        <div class="label">Health Score</div>
        <div class="value">
            <span class="badge health-badge">{_esc(health.title())}</span>
        </div>
    </div>
    """)

    parts.append(f"""
    <div class="card">
        <div class="label">Accounts / Entries</div>
        <div class="value">{total_accounts} <span style="font-size:14px;color:#8b949e">accts</span></div>
        <div style="font-size:13px;color:#8b949e;margin-top:4px">{active_accounts} active · {total_entries} entries · {reconciled_entries} reconciled</div>
    </div>
    """)

    parts.append('</div>')  # end grid

    # ── Alerts ──────────────────────────────────────────────────────

    if include_alerts and alert_triggers:
        parts.append('<div class="section">')
        parts.append('<h2>⚠️ Active Alerts</h2>')
        for trigger in alert_triggers:
            severity_class = trigger.severity if trigger.severity != "critical" else ""
            parts.append(f"""
            <div class="alert-item {severity_class}">
                <span class="severity" style="color: {'#f85149' if trigger.severity == 'critical' else '#d29922' if trigger.severity == 'warning' else '#58a6ff'}">
                    {trigger.severity.upper()}
                </span>
                · {_esc(trigger.message)}
                <span style="float:right;color:#8b949e">{trigger.triggered_at.strftime("%Y-%m-%d %H:%M")}</span>
            </div>
            """)
        parts.append('</div>')

    # ── Two Column: Balance Sheet + Income Statement ────────────────

    parts.append('<div class="two-col">')

    # Balance Sheet
    parts.append('<div class="section">')
    parts.append('<h2>Balance Sheet</h2>')
    parts.append('<table>')
    parts.append('<thead><tr><th>Account</th><th class="num">Amount</th></tr></thead>')
    parts.append('<tbody>')

    parts.append('<tr><td colspan="2" style="font-weight:600;color:#8b949e;font-size:12px;padding-top:12px;">ASSETS</td></tr>')
    for row in balance_sheet.assets:
        parts.append(f'<tr><td>{_esc(row.account_name)}</td><td class="num">{_fmt_money(row.amount)}</td></tr>')
    if not balance_sheet.assets:
        parts.append('<tr><td colspan="2" class="empty-state">No asset accounts</td></tr>')
    parts.append(f'<tr style="font-weight:700;"><td>Total Assets</td><td class="num">{_fmt_money(balance_sheet.total_assets)}</td></tr>')

    parts.append('<tr><td colspan="2" style="font-weight:600;color:#8b949e;font-size:12px;padding-top:12px;">LIABILITIES</td></tr>')
    for row in balance_sheet.liabilities:
        parts.append(f'<tr><td>{_esc(row.account_name)}</td><td class="num">{_fmt_money(row.amount)}</td></tr>')
    if not balance_sheet.liabilities:
        parts.append('<tr><td colspan="2" class="empty-state">No liability accounts</td></tr>')
    parts.append(f'<tr style="font-weight:700;"><td>Total Liabilities</td><td class="num">{_fmt_money(balance_sheet.total_liabilities)}</td></tr>')

    parts.append('<tr><td colspan="2" style="font-weight:600;color:#8b949e;font-size:12px;padding-top:12px;">EQUITY</td></tr>')
    for row in balance_sheet.equity_rows:
        parts.append(f'<tr><td>{_esc(row.account_name)}</td><td class="num">{_fmt_money(row.amount)}</td></tr>')
    parts.append(f'<tr><td>Retained Earnings</td><td class="num">{_fmt_money(balance_sheet.retained_earnings)}</td></tr>')
    total_le = balance_sheet.total_liabilities + balance_sheet.total_equity + balance_sheet.retained_earnings
    balanced = abs(balance_sheet.total_assets - total_le) < 0.01
    parts.append(f'<tr style="font-weight:700;"><td>Total L + E</td><td class="num">{_fmt_money(total_le)}</td></tr>')
    parts.append(f'<tr><td colspan="2" style="text-align:center;padding-top:8px;">'
                 f'<span class="badge" style="background:{"#27ae60" if balanced else "#e74c3c"};color:#fff;">'
                 f'{"✓ Balanced" if balanced else "✗ Out of Balance"}</span></td></tr>')

    parts.append('</tbody></table>')
    parts.append('</div>')

    # Income Statement
    parts.append('<div class="section">')
    parts.append('<h2>Income Statement</h2>')
    parts.append('<table>')
    parts.append('<thead><tr><th>Account</th><th class="num">Amount</th></tr></thead>')
    parts.append('<tbody>')

    parts.append('<tr><td colspan="2" style="font-weight:600;color:#8b949e;font-size:12px;">REVENUE</td></tr>')
    for row in income_stmt.revenue_rows:
        parts.append(f'<tr><td>{_esc(row.account_name)}</td><td class="num pos">{_fmt_money(row.amount)}</td></tr>')
    if not income_stmt.revenue_rows:
        parts.append('<tr><td colspan="2" class="empty-state">No revenue</td></tr>')
    parts.append(f'<tr style="font-weight:700;"><td>Total Revenue</td><td class="num">{_fmt_money(income_stmt.total_revenue)}</td></tr>')

    parts.append('<tr><td colspan="2" style="font-weight:600;color:#8b949e;font-size:12px;padding-top:12px;">EXPENSES</td></tr>')
    for row in income_stmt.expense_rows:
        parts.append(f'<tr><td>{_esc(row.account_name)}</td><td class="num neg">{_fmt_money(row.amount)}</td></tr>')
    if not income_stmt.expense_rows:
        parts.append('<tr><td colspan="2" class="empty-state">No expenses</td></tr>')
    parts.append(f'<tr style="font-weight:700;"><td>Total Expenses</td><td class="num">{_fmt_money(income_stmt.total_expenses)}</td></tr>')

    ni_class = "pos" if income_stmt.net_income >= 0 else "neg"
    parts.append(f'<tr style="font-weight:700;border-top:2px solid #30363d;"><td>Net Income</td><td class="num {ni_class}">{_fmt_money(income_stmt.net_income)}</td></tr>')

    parts.append('</tbody></table>')
    parts.append('</div>')

    parts.append('</div>')  # end two-col

    # ── Financial Ratios ────────────────────────────────────────────

    ratio_items = [
        ("Current Ratio", ratios.current_ratio, "≥ 1.5 is healthy"),
        ("Quick Ratio", ratios.quick_ratio, "≥ 1.0 is healthy"),
        ("Cash Ratio", ratios.cash_ratio, "Higher is safer"),
        ("Debt-to-Equity", ratios.debt_to_equity, "Lower is better"),
        ("Profit Margin", ratios.profit_margin, "Higher is better"),
        ("Return on Assets", ratios.return_on_assets, "Higher is better"),
        ("Return on Equity", ratios.return_on_equity, "Higher is better"),
        ("Asset Turnover", ratios.asset_turnover, "Higher is better"),
    ]

    parts.append('<div class="section">')
    parts.append('<h2>Financial Ratios</h2>')
    parts.append('<table>')
    parts.append('<thead><tr><th>Ratio</th><th class="num">Value</th><th>Guideline</th></tr></thead>')
    parts.append('<tbody>')
    for name, value, guideline in ratio_items:
        val_str = f"{value:.4f}" if value is not None else "N/A"
        parts.append(f'<tr><td>{name}</td><td class="num mono">{val_str}</td><td style="color:#8b949e;font-size:13px;">{_esc(guideline)}</td></tr>')
    parts.append('</tbody></table>')

    if ratios.warnings:
        parts.append('<div style="margin-top:16px;">')
        for w in ratios.warnings:
            parts.append(f'<div class="alert-item warning"><span class="severity" style="color:#d29922;">WARNING</span> · {_esc(w)}</div>')
        parts.append('</div>')

    parts.append('</div>')

    # ── Trial Balance ───────────────────────────────────────────────

    parts.append('<div class="section">')
    parts.append(f'<h2>Trial Balance <span class="badge" style="background:{"#27ae60" if trial_balance.is_balanced else "#e74c3c"};color:#fff;margin-left:8px;">{"Balanced" if trial_balance.is_balanced else "Not Balanced"}</span></h2>')
    parts.append('<table>')
    parts.append('<thead><tr><th>Code</th><th>Account</th><th class="num">Debit</th><th class="num">Credit</th></tr></thead>')
    parts.append('<tbody>')
    for row in trial_balance.rows:
        debit_str = f"{row.debit:,.2f}" if row.debit else ""
        credit_str = f"{row.credit:,.2f}" if row.credit else ""
        parts.append(f'<tr><td class="mono">{_esc(row.account_code)}</td><td>{_esc(row.account_name)}</td><td class="num mono">{debit_str}</td><td class="num mono">{credit_str}</td></tr>')
    parts.append(f'<tr style="font-weight:700;border-top:2px solid #30363d;"><td colspan="2">TOTAL</td><td class="num mono">{trial_balance.total_debits:,.2f}</td><td class="num mono">{trial_balance.total_credits:,.2f}</td></tr>')
    parts.append('</tbody></table>')
    parts.append('</div>')

    # ── Footer ──────────────────────────────────────────────────────

    parts.append(f"""
<div class="footer">
    Generated by <strong>Agent Ledger</strong> · {now.strftime("%Y-%m-%d %H:%M:%S UTC")} ·
    {total_entries} entries · {total_accounts} accounts
</div>
</div>
</body>
</html>
""")

    return "\n".join(parts)


def save_dashboard_html(
    ledger: Ledger,
    output_path: str,
    title: str = "Agent Ledger Dashboard",
    include_alerts: bool = True,
) -> str:
    """Generate and save an HTML dashboard to a file.

    Args:
        ledger: The ledger to dashboard
        output_path: Path to save the HTML file
        title: HTML page title
        include_alerts: Whether to include the alerts section

    Returns:
        The path that was written to
    """
    html_content = generate_dashboard_html(
        ledger,
        title=title,
        include_alerts=include_alerts,
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return output_path
