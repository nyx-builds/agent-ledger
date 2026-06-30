"""CLI for agent-ledger — Double-entry accounting for autonomous agents."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .models import AccountType, JournalLine
from .storage import Storage
from .ledger import Ledger
from .reports import (
    generate_trial_balance, generate_income_statement, generate_balance_sheet,
    format_trial_balance, format_income_statement, format_balance_sheet,
)
from .closing import close_period
from .hierarchy import AccountHierarchy
from .export import (
    export_accounts_csv, export_entries_csv, export_trial_balance_csv,
    export_income_statement_csv, export_balance_sheet_csv,
    export_account_transactions_csv, export_hierarchy_csv, write_csv_to_file,
)
from .audit import AuditAction
from .exceptions import LedgerError

console = Console()

DEFAULT_LEDGER_PATH = Path.cwd() / "ledger.json"


def _parse_cli_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 date string from CLI input."""
    if date_str is None:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        console.print(f"[red]Invalid date format:[/red] {date_str}. Use ISO 8601 (e.g. 2024-01-31)")
        sys.exit(1)


def get_ledger(path: Optional[Path] = None) -> Ledger:
    """Get a Ledger instance from the given path."""
    filepath = path or DEFAULT_LEDGER_PATH
    storage = Storage(filepath)
    return Ledger(storage)


def handle_error(func):
    """Decorator to handle LedgerError exceptions in CLI."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except LedgerError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)
        except FileExistsError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)
    # Preserve the original function name for Click
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


@click.group()
@click.option("--ledger-file", "-f", type=click.Path(), default=None,
              help="Path to ledger JSON file")
@click.pass_context
def cli(ctx, ledger_file):
    """Agent Ledger — Double-entry accounting for autonomous agents."""
    ctx.ensure_object(dict)
    ctx.obj["ledger_file"] = Path(ledger_file) if ledger_file else DEFAULT_LEDGER_PATH


@cli.command()
@click.option("--name", "-n", default="Default Ledger", help="Ledger name")
@click.option("--base-currency", "-c", default="USD", help="Base currency code")
@click.pass_context
@handle_error
def init(ctx, name, base_currency):
    """Initialize a new ledger."""
    filepath = ctx.obj["ledger_file"]
    storage = Storage(filepath)
    data = storage.init(name=name, base_currency=base_currency)
    console.print(f"[green]✓[/green] Ledger initialized at {filepath}")
    console.print(f"  Name: {data.name}")
    console.print(f"  Base Currency: {data.base_currency}")


# ── Account Commands ────────────────────────────────────────────

@cli.group()
def account():
    """Manage chart of accounts."""
    pass


@account.command("create")
@click.argument("code")
@click.argument("name")
@click.option("--type", "-t", "account_type", required=True,
              type=click.Choice([t.value for t in AccountType]),
              help="Account type")
@click.option("--currency", "-c", default="USD", help="Account currency")
@click.option("--description", "-d", default="", help="Account description")
@click.option("--parent", "-p", default=None, help="Parent account code")
@click.pass_context
@handle_error
def account_create(ctx, code, name, account_type, currency, description, parent):
    """Create a new account."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    account = ledger.create_account(
        code=code,
        name=name,
        account_type=AccountType(account_type),
        currency=currency,
        description=description,
        parent_code=parent,
    )
    console.print(f"[green]✓[/green] Account created: {account.code} - {account.name} ({account.account_type.value})")


@account.command("list")
@click.option("--type", "-t", "account_type", default=None,
              type=click.Choice([t.value for t in AccountType]),
              help="Filter by account type")
@click.option("--active-only", is_flag=True, help="Show only active accounts")
@click.pass_context
@handle_error
def account_list(ctx, account_type, active_only):
    """List all accounts."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    at = AccountType(account_type) if account_type else None
    accounts = ledger.list_accounts(account_type=at, active_only=active_only)

    if not accounts:
        console.print("[yellow]No accounts found[/yellow]")
        return

    table = Table(title="Chart of Accounts")
    table.add_column("Code", style="cyan")
    table.add_column("Name")
    table.add_column("Type", style="magenta")
    table.add_column("Currency", style="green")
    table.add_column("Balance", justify="right", style="yellow")
    table.add_column("Parent", style="dim")
    table.add_column("Active")

    for acct in accounts:
        balance = ledger.get_account_balance(acct.code)
        table.add_row(
            acct.code,
            acct.name,
            acct.account_type.value,
            acct.currency,
            f"{balance.balance:,.2f}",
            acct.parent_code or "",
            "✓" if acct.active else "✗",
        )

    console.print(table)


@account.command("show")
@click.argument("code")
@click.pass_context
@handle_error
def account_show(ctx, code):
    """Show account details and transactions."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    account = ledger.get_account(code)
    balance = ledger.get_account_balance(code)
    transactions = ledger.get_account_transactions(code)

    console.print(Panel(
        f"[cyan]{account.code}[/cyan] - {account.name}\n"
        f"Type: {account.account_type.value}  |  Currency: {account.currency}\n"
        f"Balance: {balance.balance:,.2f}  |  Raw: {balance.raw_balance:,.2f}\n"
        f"Debits: {balance.debit_total:,.2f}  |  Credits: {balance.credit_total:,.2f}\n"
        f"Parent: {account.parent_code or 'None'}",
        title="Account Details",
    ))

    if transactions:
        table = Table(title="Transactions")
        table.add_column("Date", style="cyan")
        table.add_column("Description")
        table.add_column("Debit", justify="right", style="green")
        table.add_column("Credit", justify="right", style="red")
        table.add_column("Balance", justify="right", style="yellow")
        table.add_column("Reconciled")

        for tx in transactions:
            date_str = tx["timestamp"].strftime("%Y-%m-%d")
            debit_str = f"{tx['debit']:,.2f}" if tx["debit"] else ""
            credit_str = f"{tx['credit']:,.2f}" if tx["credit"] else ""
            table.add_row(
                date_str,
                tx["description"],
                debit_str,
                credit_str,
                f"{tx['balance']:,.2f}",
                "✓" if tx["reconciled"] else "",
            )

        console.print(table)


@account.command("update")
@click.argument("code")
@click.option("--name", "-n", default=None, help="New account name")
@click.option("--description", "-d", default=None, help="New description")
@click.option("--active/--inactive", default=None, help="Set active status")
@click.pass_context
@handle_error
def account_update(ctx, code, name, description, active):
    """Update an existing account."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    account = ledger.update_account(code, name=name, description=description, active=active)
    console.print(f"[green]✓[/green] Account updated: {account.code} - {account.name}")


@account.command("delete")
@click.argument("code")
@click.pass_context
@handle_error
def account_delete(ctx, code):
    """Delete an account (must have zero balance and no children)."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    ledger.delete_account(code)
    console.print(f"[green]✓[/green] Account '{code}' deleted")


# ── Journal Entry Commands ──────────────────────────────────────

@cli.group("entry")
def entry():
    """Manage journal entries."""
    pass


@entry.command("post")
@click.argument("description")
@click.argument("lines", nargs=-1, required=True)
@click.option("--tag", "-t", multiple=True, help="Tags for the entry")
@click.pass_context
@handle_error
def entry_post(ctx, description, lines, tag):
    """Post a journal entry.

    Lines format: account:amount (debit) or account:-amount (credit)

    Example: agent-ledger entry post "Sale" cash:1000 revenue:-1000
    """
    ledger = get_ledger(ctx.obj["ledger_file"])
    journal_lines = []

    for line_str in lines:
        try:
            account_code, amount_str = line_str.split(":", 1)
            amount = float(amount_str)
        except (ValueError, IndexError):
            console.print(f"[red]Invalid line format:[/red] {line_str}")
            console.print("Expected format: account:amount or account:-amount")
            sys.exit(1)

        if amount > 0:
            journal_lines.append(JournalLine(
                account_code=account_code.strip().lower(),
                debit=round(abs(amount), 2),
                credit=0.0,
            ))
        elif amount < 0:
            journal_lines.append(JournalLine(
                account_code=account_code.strip().lower(),
                debit=0.0,
                credit=round(abs(amount), 2),
            ))
        else:
            console.print(f"[red]Invalid amount:[/red] {line_str} — amount cannot be zero")
            sys.exit(1)

    entry_obj = ledger.post_entry(
        description=description,
        lines=journal_lines,
        tags=list(tag),
    )

    console.print(f"[green]✓[/green] Entry posted: {entry_obj.id}")
    console.print(f"  Description: {entry_obj.description}")
    console.print(f"  Debits: {entry_obj.total_debits:,.2f}  |  Credits: {entry_obj.total_credits:,.2f}")


@entry.command("list")
@click.option("--account", "-a", default=None, help="Filter by account code")
@click.option("--tag", "-t", default=None, help="Filter by tag")
@click.option("--limit", "-l", default=20, help="Maximum entries to show")
@click.pass_context
@handle_error
def entry_list(ctx, account, tag, limit):
    """List journal entries."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    entries = ledger.list_entries(account_code=account, tag=tag)

    if not entries:
        console.print("[yellow]No entries found[/yellow]")
        return

    entries = entries[-limit:]

    table = Table(title="Journal Entries")
    table.add_column("ID", style="cyan", max_width=8)
    table.add_column("Date", style="cyan")
    table.add_column("Description")
    table.add_column("Debits", justify="right", style="green")
    table.add_column("Credits", justify="right", style="red")
    table.add_column("Reconciled")

    for e in entries:
        date_str = e.timestamp.strftime("%Y-%m-%d")
        table.add_row(
            e.id[:8],
            date_str,
            e.description[:40],
            f"{e.total_debits:,.2f}",
            f"{e.total_credits:,.2f}",
            "✓" if e.reconciled else "",
        )

    console.print(table)


@entry.command("show")
@click.argument("entry_id")
@click.pass_context
@handle_error
def entry_show(ctx, entry_id):
    """Show details of a journal entry."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    entry_obj = ledger.get_entry(entry_id)

    console.print(Panel(
        f"[cyan]ID:[/cyan] {entry_obj.id}\n"
        f"[cyan]Description:[/cyan] {entry_obj.description}\n"
        f"[cyan]Date:[/cyan] {entry_obj.timestamp.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"[cyan]Tags:[/cyan] {', '.join(entry_obj.tags) or 'None'}\n"
        f"[cyan]Reconciled:[/cyan] {'Yes' if entry_obj.reconciled else 'No'}",
        title="Journal Entry",
    ))

    table = Table()
    table.add_column("Account", style="cyan")
    table.add_column("Debit", justify="right", style="green")
    table.add_column("Credit", justify="right", style="red")
    table.add_column("Description")

    for line in entry_obj.lines:
        debit_str = f"{line.debit:,.2f}" if line.debit else ""
        credit_str = f"{line.credit:,.2f}" if line.credit else ""
        table.add_row(line.account_code, debit_str, credit_str, line.description)

    console.print(table)
    console.print(f"  Total Debits: {entry_obj.total_debits:,.2f}  |  Total Credits: {entry_obj.total_credits:,.2f}")


@entry.command("delete")
@click.argument("entry_id")
@click.pass_context
@handle_error
def entry_delete(ctx, entry_id):
    """Delete a journal entry."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    ledger.delete_entry(entry_id)
    console.print(f"[green]✓[/green] Entry '{entry_id}' deleted")


@entry.command("reconcile")
@click.argument("entry_id")
@click.pass_context
@handle_error
def entry_reconcile(ctx, entry_id):
    """Mark a journal entry as reconciled."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    entry_obj = ledger.reconcile_entry(entry_id)
    console.print(f"[green]✓[/green] Entry '{entry_id}' reconciled")


@entry.command("unreconcile")
@click.argument("entry_id")
@click.pass_context
@handle_error
def entry_unreconcile(ctx, entry_id):
    """Mark a journal entry as unreconciled."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    entry_obj = ledger.unreconcile_entry(entry_id)
    console.print(f"[green]✓[/green] Entry '{entry_id}' unreconciled")


@entry.command("reverse")
@click.argument("entry_id")
@click.option("--reason", "-r", default=None, help="Reason for reversal")
@click.pass_context
@handle_error
def entry_reverse(ctx, entry_id, reason):
    """Reverse a journal entry (creates an opposing entry)."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    reversal = ledger.reverse_entry(entry_id, reason=reason)
    console.print(f"[green]✓[/green] Entry reversed")
    console.print(f"  Original: {entry_id}")
    console.print(f"  Reversal: {reversal.id}")
    console.print(f"  Description: {reversal.description}")


# ── Report Commands ─────────────────────────────────────────────

@cli.group("report")
def report():
    """Generate financial reports."""
    pass


@report.command("trial-balance")
@click.option("--as-of", "-a", default=None, help="As-of date (ISO 8601, e.g. 2024-01-31)")
@click.pass_context
@handle_error
def report_trial_balance(ctx, as_of):
    """Generate a trial balance."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    as_of_dt = _parse_cli_date(as_of)
    tb = generate_trial_balance(ledger, as_of=as_of_dt)
    console.print(format_trial_balance(tb))


@report.command("income-statement")
@click.option("--from-date", "-f", default=None, help="Start date (ISO 8601)")
@click.option("--to-date", "-t", default=None, help="End date (ISO 8601)")
@click.pass_context
@handle_error
def report_income_statement(ctx, from_date, to_date):
    """Generate an income statement."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    fd = _parse_cli_date(from_date)
    td = _parse_cli_date(to_date)
    ist = generate_income_statement(ledger, from_date=fd, to_date=td)
    console.print(format_income_statement(ist))


@report.command("balance-sheet")
@click.option("--as-of", "-a", default=None, help="As-of date (ISO 8601)")
@click.pass_context
@handle_error
def report_balance_sheet(ctx, as_of):
    """Generate a balance sheet."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    as_of_dt = _parse_cli_date(as_of)
    bs = generate_balance_sheet(ledger, as_of=as_of_dt)
    console.print(format_balance_sheet(bs))


@report.command("cash-flow")
@click.pass_context
@handle_error
def report_cash_flow(ctx):
    """Generate a cash flow statement."""
    from .cashflow import generate_cash_flow_statement, format_cash_flow_statement
    ledger = get_ledger(ctx.obj["ledger_file"])
    cf = generate_cash_flow_statement(ledger)
    console.print(format_cash_flow_statement(cf))


# ── Currency Commands ───────────────────────────────────────────

@cli.group("currency")
def currency():
    """Manage currencies and exchange rates."""
    pass


@currency.command("add-rate")
@click.argument("from_currency")
@click.argument("to_currency")
@click.argument("rate", type=float)
@click.option("--source", "-s", default="manual", help="Rate source")
@click.pass_context
@handle_error
def currency_add_rate(ctx, from_currency, to_currency, rate, source):
    """Add an exchange rate."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    er = ledger.add_exchange_rate(from_currency, to_currency, rate, source)
    console.print(f"[green]✓[/green] Rate added: 1 {er.from_currency} = {er.rate} {er.to_currency}")


@currency.command("convert")
@click.argument("amount", type=float)
@click.argument("from_currency")
@click.argument("to_currency")
@click.pass_context
@handle_error
def currency_convert(ctx, amount, from_currency, to_currency):
    """Convert an amount between currencies."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    converter = ledger.get_currency_converter()
    result = converter.convert(amount, from_currency, to_currency)
    console.print(f"{amount:,.2f} {from_currency} = {result:,.2f} {to_currency}")


@currency.command("list-rates")
@click.pass_context
@handle_error
def currency_list_rates(ctx):
    """List all exchange rates."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    converter = ledger.get_currency_converter()
    rates = converter.list_rates()

    if not rates:
        console.print("[yellow]No exchange rates found[/yellow]")
        return

    table = Table(title="Exchange Rates")
    table.add_column("From", style="cyan")
    table.add_column("To", style="cyan")
    table.add_column("Rate", justify="right", style="green")
    table.add_column("Source")
    table.add_column("Date")

    for r in rates:
        table.add_row(
            r.from_currency,
            r.to_currency,
            f"{r.rate:.6f}",
            r.source,
            r.timestamp.strftime("%Y-%m-%d"),
        )

    console.print(table)


# ── Period Close Commands ──────────────────────────────────────

@cli.group("period")
def period():
    """Manage accounting periods."""
    pass


@period.command("close")
@click.option("--retained-earnings", "-r", default="retained_earnings",
              help="Retained earnings account code")
@click.option("--description", "-d", default=None,
              help="Custom description for the closing entry")
@click.pass_context
@handle_error
def period_close(ctx, retained_earnings, description):
    """Close the current period (zero out temporary accounts)."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    result = close_period(
        ledger,
        retained_earnings_code=retained_earnings,
        description=description,
    )

    console.print(f"[green]✓[/green] Period closed successfully!")
    console.print(f"  Closing Entry ID: {result.closing_entry.id}")
    console.print(f"  Revenue accounts closed: {len(result.revenue_accounts_closed)}")
    console.print(f"  Expense accounts closed: {len(result.expense_accounts_closed)}")
    console.print(f"  Net Income: {result.net_income:,.2f}")
    console.print(f"  Retained Earnings → {result.retained_earnings_account}")
    console.print(f"  Closed at: {result.closed_at.strftime('%Y-%m-%d %H:%M UTC')}")


@period.command("list-closes")
@click.pass_context
@handle_error
def period_list_closes(ctx):
    """List all period close records."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    closes = ledger.get_closed_periods()

    if not closes:
        console.print("[yellow]No period closes recorded[/yellow]")
        return

    table = Table(title="Closed Periods")
    table.add_column("Entry ID", style="cyan", max_width=8)
    table.add_column("Closed At")
    table.add_column("Net Income", justify="right", style="green")
    table.add_column("Revenue Accounts", justify="right")
    table.add_column("Expense Accounts", justify="right")

    for c in closes:
        table.add_row(
            c.get("closing_entry_id", "")[:8],
            c.get("closed_at", "")[:19],
            f"{c.get('net_income', 0):,.2f}",
            str(c.get("revenue_accounts_closed", 0)),
            str(c.get("expense_accounts_closed", 0)),
        )

    console.print(table)


# ── Hierarchy Commands ─────────────────────────────────────────

@cli.group("hierarchy")
def hierarchy():
    """Manage account hierarchy."""
    pass


@hierarchy.command("tree")
@click.option("--root", "-r", default=None, help="Root account code")
@click.pass_context
@handle_error
def hierarchy_tree(ctx, root):
    """Display account hierarchy tree."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    h = AccountHierarchy(ledger)
    tree_str = h.format_tree(root_code=root)
    console.print(tree_str)


@hierarchy.command("rollup")
@click.argument("account_code")
@click.pass_context
@handle_error
def hierarchy_rollup(ctx, account_code):
    """Show rolled-up balance for an account (including descendants)."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    h = AccountHierarchy(ledger)
    own_balance = ledger.get_account_balance(account_code)
    rollup_balance = h.get_rollup_balance(account_code)
    account = ledger.get_account(account_code)

    console.print(Panel(
        f"[cyan]{account.code}[/cyan] - {account.name}\n"
        f"Own Balance: {own_balance.balance:,.2f}\n"
        f"Rollup Balance: {rollup_balance.balance:,.2f}\n"
        f"Children: {len(h.get_children(account_code))}",
        title="Account Rollup",
    ))


@hierarchy.command("validate")
@click.pass_context
@handle_error
def hierarchy_validate(ctx):
    """Validate the account hierarchy for issues."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    h = AccountHierarchy(ledger)
    warnings = h.validate_hierarchy()

    if not warnings:
        console.print("[green]✓[/green] No hierarchy issues found")
    else:
        for w in warnings:
            console.print(f"[yellow]⚠[/yellow] {w}")


# ── Audit Log Commands ─────────────────────────────────────────

@cli.group("audit")
def audit():
    """View audit log."""
    pass


@audit.command("list")
@click.option("--action", "-a", default=None,
              type=click.Choice([a.value for a in AuditAction]),
              help="Filter by action type")
@click.option("--actor", default=None, help="Filter by actor")
@click.option("--limit", "-l", default=50, help="Maximum entries to show")
@click.pass_context
@handle_error
def audit_list(ctx, action, actor, limit):
    """View audit log entries."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    action_enum = AuditAction(action) if action else None
    entries = ledger.audit.list_entries(action=action_enum, actor=actor, limit=limit)

    if not entries:
        console.print("[yellow]No audit entries found[/yellow]")
        return

    table = Table(title="Audit Log")
    table.add_column("Time", style="cyan")
    table.add_column("Action", style="magenta")
    table.add_column("Actor", style="green")
    table.add_column("Details", max_width=60)

    for e in entries:
        time_str = e.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        details_str = ", ".join(f"{k}={v}" for k, v in list(e.details.items())[:3])
        table.add_row(time_str, e.action.value, e.actor, details_str)

    console.print(table)


@audit.command("show")
@click.argument("entry_id")
@click.pass_context
@handle_error
def audit_show(ctx, entry_id):
    """Show details of an audit entry."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    entry = ledger.audit.get_entry(entry_id)

    before_str = ""
    after_str = ""
    if entry.before:
        before_str = ", ".join(f"{k}={v}" for k, v in entry.before.items())
    if entry.after:
        after_str = ", ".join(f"{k}={v}" for k, v in entry.after.items())

    console.print(Panel(
        f"[cyan]ID:[/cyan] {entry.id}\n"
        f"[cyan]Action:[/cyan] {entry.action.value}\n"
        f"[cyan]Timestamp:[/cyan] {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"[cyan]Actor:[/cyan] {entry.actor}\n"
        f"[cyan]Details:[/cyan] {entry.details}\n"
        f"[cyan]Before:[/cyan] {before_str or 'N/A'}\n"
        f"[cyan]After:[/cyan] {after_str or 'N/A'}",
        title="Audit Entry",
    ))


@audit.command("count")
@click.pass_context
@handle_error
def audit_count(ctx):
    """Show total audit entry count."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    console.print(f"Audit entries: {ledger.audit.count}")


# ── Export Commands ─────────────────────────────────────────────

@cli.group("export")
def export():
    """Export data to CSV."""
    pass


@export.command("accounts")
@click.option("--output", "-o", default=None, help="Output file path (prints to stdout if not specified)")
@click.pass_context
@handle_error
def export_accounts(ctx, output):
    """Export chart of accounts to CSV."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    csv_content = export_accounts_csv(ledger)
    if output:
        write_csv_to_file(csv_content, output)
        console.print(f"[green]✓[/green] Accounts exported to {output}")
    else:
        console.print(csv_content)


@export.command("entries")
@click.option("--account", "-a", default=None, help="Filter by account code")
@click.option("--tag", "-t", default=None, help="Filter by tag")
@click.option("--output", "-o", default=None, help="Output file path")
@click.pass_context
@handle_error
def export_entries(ctx, account, tag, output):
    """Export journal entries to CSV."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    csv_content = export_entries_csv(ledger, account_code=account, tag=tag)
    if output:
        write_csv_to_file(csv_content, output)
        console.print(f"[green]✓[/green] Entries exported to {output}")
    else:
        console.print(csv_content)


@export.command("trial-balance")
@click.option("--output", "-o", default=None, help="Output file path")
@click.pass_context
@handle_error
def export_trial_balance(ctx, output):
    """Export trial balance to CSV."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    csv_content = export_trial_balance_csv(ledger)
    if output:
        write_csv_to_file(csv_content, output)
        console.print(f"[green]✓[/green] Trial balance exported to {output}")
    else:
        console.print(csv_content)


@export.command("income-statement")
@click.option("--output", "-o", default=None, help="Output file path")
@click.pass_context
@handle_error
def export_income_statement(ctx, output):
    """Export income statement to CSV."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    csv_content = export_income_statement_csv(ledger)
    if output:
        write_csv_to_file(csv_content, output)
        console.print(f"[green]✓[/green] Income statement exported to {output}")
    else:
        console.print(csv_content)


@export.command("balance-sheet")
@click.option("--output", "-o", default=None, help="Output file path")
@click.pass_context
@handle_error
def export_balance_sheet(ctx, output):
    """Export balance sheet to CSV."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    csv_content = export_balance_sheet_csv(ledger)
    if output:
        write_csv_to_file(csv_content, output)
        console.print(f"[green]✓[/green] Balance sheet exported to {output}")
    else:
        console.print(csv_content)


@export.command("hierarchy")
@click.option("--output", "-o", default=None, help="Output file path")
@click.pass_context
@handle_error
def export_hierarchy(ctx, output):
    """Export account hierarchy with rollup balances to CSV."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    csv_content = export_hierarchy_csv(ledger)
    if output:
        write_csv_to_file(csv_content, output)
        console.print(f"[green]✓[/green] Hierarchy exported to {output}")
    else:
        console.print(csv_content)


# ── Template Commands ─────────────────────────────────────────

@cli.group("template")
def template():
    """Manage chart of accounts templates."""
    pass


@template.command("list")
@click.pass_context
@handle_error
def template_list(ctx):
    """List available chart of accounts templates."""
    from .templates import get_template_names
    templates = get_template_names()

    table = Table(title="Account Templates")
    table.add_column("Key", style="cyan")
    table.add_column("Name")
    table.add_column("Accounts", justify="right", style="green")

    for t in templates:
        table.add_row(t["key"], t["name"], str(t["account_count"]))

    console.print(table)


@template.command("apply")
@click.argument("template_key", type=click.Choice(["solo", "startup", "freelancer"]))
@click.pass_context
@handle_error
def template_apply(ctx, template_key):
    """Apply a chart of accounts template."""
    from .templates import apply_template
    ledger = get_ledger(ctx.obj["ledger_file"])
    created = apply_template(ledger, template_key)
    console.print(f"[green]✓[/green] Applied '{template_key}' template: {len(created)} accounts created")


# ── Import Commands ───────────────────────────────────────────

@cli.group("import")
def import_group():
    """Import data from CSV."""
    pass


@import_group.command("accounts")
@click.argument("csv_file", type=click.Path(exists=True))
@click.option("--skip-errors", is_flag=True, help="Skip rows with errors")
@click.pass_context
@handle_error
def import_accounts(ctx, csv_file, skip_errors):
    """Import accounts from a CSV file."""
    from .import_csv import import_accounts_csv
    from pathlib import Path

    csv_content = Path(csv_file).read_text(encoding="utf-8")
    ledger = get_ledger(ctx.obj["ledger_file"])
    result = import_accounts_csv(ledger, csv_content, skip_errors=skip_errors)

    console.print(f"[green]✓[/green] Imported {result.imported} accounts")
    if result.skipped:
        console.print(f"  [yellow]Skipped: {result.skipped}[/yellow]")
    if result.errors:
        for err in result.errors:
            console.print(f"  [red]Error: {err}[/red]")


@import_group.command("entries")
@click.argument("csv_file", type=click.Path(exists=True))
@click.option("--skip-errors", is_flag=True, help="Skip rows with errors")
@click.pass_context
@handle_error
def import_entries(ctx, csv_file, skip_errors):
    """Import journal entries from a CSV file."""
    from .import_csv import import_entries_csv
    from pathlib import Path

    csv_content = Path(csv_file).read_text(encoding="utf-8")
    ledger = get_ledger(ctx.obj["ledger_file"])
    result = import_entries_csv(ledger, csv_content, skip_errors=skip_errors)

    console.print(f"[green]✓[/green] Imported {result.imported} entries")
    if result.skipped:
        console.print(f"  [yellow]Skipped: {result.skipped}[/yellow]")
    if result.errors:
        for err in result.errors:
            console.print(f"  [red]Error: {err}[/red]")


# ── Reconciliation Commands ────────────────────────────────────

@cli.group("recon")
def recon():
    """Bank reconciliation management."""
    pass


@recon.command("create-statement")
@click.argument("account_code")
@click.option("--statement-date", "-d", default=None, help="Statement date (ISO 8601)")
@click.option("--opening-balance", "-o", type=float, default=0.0, help="Opening balance per bank")
@click.option("--closing-balance", "-c", type=float, required=True, help="Closing balance per bank")
@click.option("--currency", default="USD", help="Currency code")
@click.pass_context
@handle_error
def recon_create_statement(ctx, account_code, statement_date, opening_balance, closing_balance, currency):
    """Create a bank statement for reconciliation."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    sd = _parse_cli_date(statement_date)
    stmt = recon_obj.create_statement(
        account_code=account_code,
        statement_date=sd,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        currency=currency,
    )
    console.print(f"[green]✓[/green] Bank statement created: {stmt.id}")
    console.print(f"  Account: {account_code}")
    console.print(f"  Closing Balance: {closing_balance:,.2f}")


@recon.command("add-line")
@click.argument("statement_id")
@click.option("--date", "-d", default=None, help="Transaction date (ISO 8601)")
@click.option("--description", "-n", default="", help="Transaction description")
@click.option("--amount", "-a", type=float, required=True, help="Amount (positive=deposit, negative=withdrawal)")
@click.option("--reference", "-r", default="", help="Check/reference number")
@click.pass_context
@handle_error
def recon_add_line(ctx, statement_id, date, description, amount, reference):
    """Add a line to a bank statement."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    dt = _parse_cli_date(date)
    line = recon_obj.add_statement_line(
        statement_id=statement_id,
        date=dt,
        description=description,
        amount=amount,
        reference=reference,
    )
    console.print(f"[green]✓[/green] Line added: {line.id}")
    console.print(f"  Description: {description or 'N/A'}")
    console.print(f"  Amount: {amount:,.2f}")


@recon.command("import-lines")
@click.argument("statement_id")
@click.argument("csv_file", type=click.Path(exists=True))
@click.option("--skip-errors", is_flag=True, help="Skip rows with errors")
@click.pass_context
@handle_error
def recon_import_lines(ctx, statement_id, csv_file, skip_errors):
    """Import bank statement lines from CSV. Columns: date, description, amount, reference."""
    import csv
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    
    lines_data = []
    with open(csv_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lines_data.append({
                "date": row.get("date", ""),
                "description": row.get("description", ""),
                "amount": float(row.get("amount", 0)),
                "reference": row.get("reference", ""),
            })
    
    created = recon_obj.add_statement_lines_batch(statement_id, lines_data)
    console.print(f"[green]✓[/green] Imported {len(created)} lines to statement {statement_id}")


@recon.command("list-statements")
@click.option("--account", "-a", default=None, help="Filter by account code")
@click.option("--status", "-s", default=None, help="Filter by status (open, in_progress, completed)")
@click.pass_context
@handle_error
def recon_list_statements(ctx, account, status):
    """List bank statements."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    statements = recon_obj.list_statements(account_code=account, status=status)
    
    if not statements:
        console.print("[yellow]No bank statements found[/yellow]")
        return
    
    table = Table(title="Bank Statements")
    table.add_column("ID", style="cyan", max_width=8)
    table.add_column("Account")
    table.add_column("Date")
    table.add_column("Opening", justify="right")
    table.add_column("Closing", justify="right")
    table.add_column("Lines", justify="right")
    table.add_column("Status")
    
    for s in statements:
        date_str = s.statement_date.strftime("%Y-%m-%d") if s.statement_date else "N/A"
        table.add_row(
            s.id[:8],
            s.account_code,
            date_str,
            f"{s.opening_balance:,.2f}",
            f"{s.closing_balance:,.2f}",
            str(len(s.lines)),
            s.status,
        )
    
    console.print(table)


@recon.command("show-statement")
@click.argument("statement_id")
@click.pass_context
@handle_error
def recon_show_statement(ctx, statement_id):
    """Show details of a bank statement with all lines."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    stmt = recon_obj.get_statement(statement_id)
    
    date_str = stmt.statement_date.strftime("%Y-%m-%d") if stmt.statement_date else "N/A"
    console.print(Panel(
        f"[cyan]ID:[/cyan] {stmt.id}\n"
        f"[cyan]Account:[/cyan] {stmt.account_code}\n"
        f"[cyan]Date:[/cyan] {date_str}\n"
        f"[cyan]Opening:[/cyan] {stmt.opening_balance:,.2f}\n"
        f"[cyan]Closing:[/cyan] {stmt.closing_balance:,.2f}\n"
        f"[cyan]Status:[/cyan] {stmt.status}",
        title="Bank Statement",
    ))
    
    if stmt.lines:
        table = Table(title="Statement Lines")
        table.add_column("ID", style="cyan", max_width=8)
        table.add_column("Date")
        table.add_column("Description")
        table.add_column("Amount", justify="right")
        table.add_column("Reference")
        table.add_column("Status")
        table.add_column("Matched Entry", max_width=8)
        
        for line in stmt.lines:
            line_date = line.date.strftime("%Y-%m-%d") if line.date else ""
            table.add_row(
                line.id[:8],
                line_date,
                line.description[:30],
                f"{line.amount:,.2f}",
                line.reference,
                line.status,
                line.matched_entry_id[:8] if line.matched_entry_id else "",
            )
        
        console.print(table)


@recon.command("match")
@click.argument("statement_id")
@click.argument("line_id")
@click.argument("entry_id")
@click.pass_context
@handle_error
def recon_match(ctx, statement_id, line_id, entry_id):
    """Manually match a statement line to a ledger entry."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    line = recon_obj.match_entry(statement_id, line_id, entry_id)
    console.print(f"[green]✓[/green] Matched line {line_id[:8]} to entry {entry_id[:8]}")


@recon.command("unmatch")
@click.argument("statement_id")
@click.argument("line_id")
@click.pass_context
@handle_error
def recon_unmatch(ctx, statement_id, line_id):
    """Unmatch a previously matched statement line."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    line = recon_obj.unmatch_entry(statement_id, line_id)
    console.print(f"[green]✓[/green] Unmatched line {line_id[:8]}")


@recon.command("auto-match")
@click.argument("statement_id")
@click.option("--tolerance", "-t", type=float, default=0.01, help="Amount tolerance for matching")
@click.pass_context
@handle_error
def recon_auto_match(ctx, statement_id, tolerance):
    """Auto-match statement lines to ledger entries by amount."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    result = recon_obj.auto_match(statement_id, tolerance=tolerance)
    console.print(f"[green]✓[/green] Auto-matched {result['matched']} lines")


@recon.command("status")
@click.argument("statement_id")
@click.pass_context
@handle_error
def recon_status(ctx, statement_id):
    """Show reconciliation status for a bank statement."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    result = recon_obj.reconcile(statement_id)
    
    console.print(Panel(
        f"[cyan]Statement:[/cyan] {result.statement_id[:8]}\n"
        f"[cyan]Total Lines:[/cyan] {result.total_statement_lines}\n"
        f"[cyan]Matched:[/cyan] {result.matched}\n"
        f"[cyan]Unmatched:[/cyan] {result.unmatched_statement}\n"
        f"[cyan]Disputed:[/cyan] {result.disputed}\n"
        f"[cyan]Bank Balance:[/cyan] {result.statement_closing_balance:,.2f}\n"
        f"[cyan]Ledger Balance:[/cyan] {result.ledger_balance:,.2f}\n"
        f"[cyan]Difference:[/cyan] {result.difference:,.2f}\n"
        f"[cyan]Balanced:[/cyan] {'Yes' if result.is_balanced else 'No'}",
        title="Reconciliation Status",
    ))


@recon.command("complete")
@click.argument("statement_id")
@click.pass_context
@handle_error
def recon_complete(ctx, statement_id):
    """Complete a bank reconciliation (all lines must be matched)."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    result = recon_obj.complete_reconciliation(statement_id)
    console.print(f"[green]✓[/green] Reconciliation completed!")
    console.print(f"  Matched: {result.matched}  |  Difference: {result.difference:,.2f}")


@recon.command("unreconciled")
@click.argument("account_code")
@click.pass_context
@handle_error
def recon_unreconciled(ctx, account_code):
    """List unreconciled ledger entries for an account."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    entries = recon_obj.get_unreconciled_entries(account_code)
    
    if not entries:
        console.print("[green]No unreconciled entries found[/green]")
        return
    
    table = Table(title=f"Unreconciled Entries — {account_code}")
    table.add_column("Entry ID", style="cyan", max_width=8)
    table.add_column("Date")
    table.add_column("Description")
    table.add_column("Amount", justify="right")
    
    for e in entries:
        table.add_row(
            e["entry_id"][:8],
            e["date"][:10],
            e["description"][:40],
            f"{e['amount']:,.2f}",
        )
    
    console.print(table)


@recon.command("dispute")
@click.argument("statement_id")
@click.argument("line_id")
@click.option("--reason", "-r", default=None, help="Reason for dispute")
@click.pass_context
@handle_error
def recon_dispute(ctx, statement_id, line_id, reason):
    """Mark a statement line as disputed."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    line = recon_obj.mark_disputed(statement_id, line_id, reason=reason)
    console.print(f"[yellow]⚠[/yellow] Line {line_id[:8]} marked as disputed")


@recon.command("delete-statement")
@click.argument("statement_id")
@click.pass_context
@handle_error
def recon_delete_statement(ctx, statement_id):
    """Delete a bank statement."""
    from .reconciliation import BankReconciliation
    ledger = get_ledger(ctx.obj["ledger_file"])
    recon_obj = BankReconciliation(ledger)
    recon_obj.delete_statement(statement_id)
    console.print(f"[green]✓[/green] Statement {statement_id[:8]} deleted")


# ── Budget Commands ────────────────────────────────────────────

@cli.group("budget")
def budget():
    """Budget management and variance tracking."""
    pass


@budget.command("create")
@click.argument("name")
@click.option("--period-start", "-s", default=None, help="Budget period start date (ISO 8601)")
@click.option("--period-end", "-e", default=None, help="Budget period end date (ISO 8601)")
@click.option("--line", "-l", multiple=True, help="Budget line: account:amount (e.g., rent:5000)")
@click.pass_context
@handle_error
def budget_create(ctx, name, period_start, period_end, line):
    """Create a new budget."""
    from .budget import BudgetManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    bm = BudgetManager(ledger)

    budget_lines = []
    for line_str in line:
        try:
            code, amount_str = line_str.split(":", 1)
            budget_lines.append({
                "account_code": code.strip(),
                "budgeted_amount": float(amount_str),
            })
        except (ValueError, IndexError):
            console.print(f"[red]Invalid line format:[/red] {line_str}. Expected account:amount")
            sys.exit(1)

    ps = _parse_cli_date(period_start)
    pe = _parse_cli_date(period_end)
    b = bm.create_budget(
        name=name,
        period_start=ps,
        period_end=pe,
        budget_lines=budget_lines if budget_lines else None,
    )
    console.print(f"[green]✓[/green] Budget created: {b.id[:8]}")
    console.print(f"  Name: {b.name}")
    console.print(f"  Lines: {len(b.lines)}")
    console.print(f"  Total Budgeted: {b.total_budgeted:,.2f}")
    console.print(f"  Status: {b.status}")


@budget.command("list")
@click.option("--status", "-s", default=None, type=click.Choice(["draft", "active", "closed"]),
              help="Filter by status")
@click.pass_context
@handle_error
def budget_list(ctx, status):
    """List all budgets."""
    from .budget import BudgetManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    bm = BudgetManager(ledger)
    budgets = bm.list_budgets(status=status)

    if not budgets:
        console.print("[yellow]No budgets found[/yellow]")
        return

    table = Table(title="Budgets")
    table.add_column("ID", style="cyan", max_width=8)
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Lines", justify="right")
    table.add_column("Budgeted", justify="right", style="green")
    table.add_column("Actual", justify="right", style="yellow")
    table.add_column("Variance", justify="right")

    for b in budgets:
        var_str = f"{b.total_variance:+,.2f}"
        table.add_row(
            b.id[:8],
            b.name,
            b.status,
            str(len(b.lines)),
            f"{b.total_budgeted:,.2f}",
            f"{b.total_actual:,.2f}",
            var_str,
        )

    console.print(table)


@budget.command("show")
@click.argument("budget_id")
@click.pass_context
@handle_error
def budget_show(ctx, budget_id):
    """Show budget details and variance report."""
    from .budget import BudgetManager, format_variance_report
    ledger = get_ledger(ctx.obj["ledger_file"])
    bm = BudgetManager(ledger)
    report = bm.get_variance_report(budget_id)
    console.print(format_variance_report(report))


@budget.command("add-line")
@click.argument("budget_id")
@click.argument("account_code")
@click.argument("amount", type=float)
@click.pass_context
@handle_error
def budget_add_line(ctx, budget_id, account_code, amount):
    """Add or update a budget line."""
    from .budget import BudgetManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    bm = BudgetManager(ledger)
    line = bm.add_budget_line(budget_id, account_code, amount)
    console.print(f"[green]✓[/green] Budget line added: {line.account_code} = {line.budgeted_amount:,.2f}")


@budget.command("remove-line")
@click.argument("budget_id")
@click.argument("account_code")
@click.pass_context
@handle_error
def budget_remove_line(ctx, budget_id, account_code):
    """Remove a budget line."""
    from .budget import BudgetManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    bm = BudgetManager(ledger)
    bm.remove_budget_line(budget_id, account_code)
    console.print(f"[green]✓[/green] Budget line removed: {account_code}")


@budget.command("activate")
@click.argument("budget_id")
@click.pass_context
@handle_error
def budget_activate(ctx, budget_id):
    """Activate a draft budget."""
    from .budget import BudgetManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    bm = BudgetManager(ledger)
    b = bm.activate_budget(budget_id)
    console.print(f"[green]✓[/green] Budget '{b.name}' activated")


@budget.command("close")
@click.argument("budget_id")
@click.pass_context
@handle_error
def budget_close(ctx, budget_id):
    """Close an active budget."""
    from .budget import BudgetManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    bm = BudgetManager(ledger)
    b = bm.close_budget(budget_id)
    console.print(f"[green]✓[/green] Budget '{b.name}' closed")


@budget.command("delete")
@click.argument("budget_id")
@click.pass_context
@handle_error
def budget_delete(ctx, budget_id):
    """Delete a draft budget."""
    from .budget import BudgetManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    bm = BudgetManager(ledger)
    bm.delete_budget(budget_id)
    console.print(f"[green]✓[/green] Budget deleted")


# ── Fiscal Year Commands ──────────────────────────────────────

@cli.group("fiscal")
def fiscal():
    """Fiscal year management."""
    pass


@fiscal.command("create")
@click.argument("name")
@click.argument("start_date")
@click.argument("end_date")
@click.option("--period-type", "-p", default="month", type=click.Choice(["month", "quarter"]),
              help="Period type to generate")
@click.pass_context
@handle_error
def fiscal_create(ctx, name, start_date, end_date, period_type):
    """Create a fiscal year (dates in ISO 8601, e.g., 2024-01-01 2024-12-31)."""
    from .fiscal import FiscalYearManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    fm = FiscalYearManager(ledger)
    sd = _parse_cli_date(start_date)
    ed = _parse_cli_date(end_date)
    if sd is None or ed is None:
        console.print("[red]Error:[/red] Invalid date format")
        sys.exit(1)
    fy = fm.create_fiscal_year(
        name=name,
        start_date=sd,
        end_date=ed,
        period_type=period_type,
    )
    console.print(f"[green]✓[/green] Fiscal year created: {fy.name}")
    console.print(f"  ID: {fy.id[:8]}")
    console.print(f"  Periods: {len(fy.periods)}")
    console.print(f"  Status: {fy.status}")


@fiscal.command("list")
@click.option("--status", "-s", default=None, type=click.Choice(["open", "closed"]),
              help="Filter by status")
@click.pass_context
@handle_error
def fiscal_list(ctx, status):
    """List fiscal years."""
    from .fiscal import FiscalYearManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    fm = FiscalYearManager(ledger)
    years = fm.list_fiscal_years(status=status)

    if not years:
        console.print("[yellow]No fiscal years found[/yellow]")
        return

    table = Table(title="Fiscal Years")
    table.add_column("ID", style="cyan", max_width=8)
    table.add_column("Name")
    table.add_column("Start")
    table.add_column("End")
    table.add_column("Periods", justify="right")
    table.add_column("Status")

    for fy in years:
        table.add_row(
            fy.id[:8],
            fy.name,
            fy.start_date.strftime("%Y-%m-%d"),
            fy.end_date.strftime("%Y-%m-%d"),
            str(len(fy.periods)),
            fy.status,
        )

    console.print(table)


@fiscal.command("close")
@click.argument("fy_id")
@click.pass_context
@handle_error
def fiscal_close(ctx, fy_id):
    """Close a fiscal year."""
    from .fiscal import FiscalYearManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    fm = FiscalYearManager(ledger)
    fy = fm.close_fiscal_year(fy_id)
    console.print(f"[green]✓[/green] Fiscal year '{fy.name}' closed")


@fiscal.command("active")
@click.pass_context
@handle_error
def fiscal_active(ctx):
    """Show the active fiscal year."""
    from .fiscal import FiscalYearManager
    ledger = get_ledger(ctx.obj["ledger_file"])
    fm = FiscalYearManager(ledger)
    fy = fm.get_active_fiscal_year()
    if fy is None:
        console.print("[yellow]No active fiscal year found[/yellow]")
        return
    console.print(Panel(
        f"[cyan]ID:[/cyan] {fy.id[:8]}\n"
        f"[cyan]Name:[/cyan] {fy.name}\n"
        f"[cyan]Start:[/cyan] {fy.start_date.strftime('%Y-%m-%d')}\n"
        f"[cyan]End:[/cyan] {fy.end_date.strftime('%Y-%m-%d')}\n"
        f"[cyan]Periods:[/cyan] {len(fy.periods)}\n"
        f"[cyan]Status:[/cyan] {fy.status}",
        title="Active Fiscal Year",
    ))


# ── Additional Report Commands ─────────────────────────────────

@report.command("tax-summary")
@click.option("--from-date", "-f", default=None, help="Start date (ISO 8601)")
@click.option("--to-date", "-t", default=None, help="End date (ISO 8601)")
@click.option("--tax-rate", "-r", type=float, default=0.0, help="Tax rate for estimation (e.g., 0.21)")
@click.pass_context
@handle_error
def report_tax_summary(ctx, from_date, to_date, tax_rate):
    """Generate a tax summary report."""
    from .tax import generate_tax_summary, format_tax_summary
    ledger = get_ledger(ctx.obj["ledger_file"])
    fd = _parse_cli_date(from_date)
    td = _parse_cli_date(to_date)
    report = generate_tax_summary(ledger, from_date=fd, to_date=td, tax_rate=tax_rate)
    console.print(format_tax_summary(report))


@report.command("general-ledger")
@click.option("--account", "-a", default=None, help="Filter by account code")
@click.option("--from-date", "-f", default=None, help="Start date (ISO 8601)")
@click.option("--to-date", "-t", default=None, help="End date (ISO 8601)")
@click.option("--tag", "-g", default=None, help="Filter by tag")
@click.pass_context
@handle_error
def report_general_ledger(ctx, account, from_date, to_date, tag):
    """Generate a General Ledger report (detailed journal with running balances)."""
    from .general_ledger import generate_general_ledger, format_general_ledger
    ledger = get_ledger(ctx.obj["ledger_file"])
    fd = _parse_cli_date(from_date)
    td = _parse_cli_date(to_date)
    report = generate_general_ledger(
        ledger,
        account_code=account,
        from_date=fd,
        to_date=td,
        tag=tag,
    )
    console.print(format_general_ledger(report))


# ── v0.6.0: Search & Account Tags ───────────────────────────────

@cli.command("search")
@click.argument("query")
@click.option("--account", "-a", default=None, help="Filter by account code")
@click.option("--tag", "-g", default=None, help="Filter by tag")
@click.option("--limit", "-l", default=20, type=int, help="Max results")
@click.pass_context
@handle_error
def search_entries(ctx, query, account, tag, limit):
    """Search journal entries by description (case-insensitive)."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    results = ledger.search_entries(query=query, account_code=account, tag=tag, limit=limit)

    if not results:
        console.print("[yellow]No matching entries found[/yellow]")
        return

    table = Table(title=f"Search Results: '{query}'")
    table.add_column("ID", style="cyan", max_width=8)
    table.add_column("Date")
    table.add_column("Description", max_width=40)
    table.add_column("Debit", justify="right")
    table.add_column("Credit", justify="right")
    table.add_column("Tags")

    for e in results:
        total_debit = sum(l.debit for l in e.lines)
        total_credit = sum(l.credit for l in e.lines)
        table.add_row(
            e.id[:8],
            e.timestamp.strftime("%Y-%m-%d"),
            e.description[:40],
            f"{total_debit:,.2f}" if total_debit else "",
            f"{total_credit:,.2f}" if total_credit else "",
            ", ".join(e.tags) if e.tags else "",
        )

    console.print(table)
    console.print(f"[dim]Found {len(results)} matching entries[/dim]")


@account.command("tag")
@click.argument("code")
@click.argument("tag")
@click.pass_context
@handle_error
def account_add_tag(ctx, code, tag):
    """Add a tag to an account."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    account = ledger.get_account(code)
    if tag not in account.tags:
        account.tags.append(tag)
        ledger.save()
    console.print(f"[green]✓[/green] Tag '{tag}' added to account {code}")


@account.command("untag")
@click.argument("code")
@click.argument("tag")
@click.pass_context
@handle_error
def account_remove_tag(ctx, code, tag):
    """Remove a tag from an account."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    account = ledger.get_account(code)
    if tag in account.tags:
        account.tags.remove(tag)
        ledger.save()
    console.print(f"[green]✓[/green] Tag '{tag}' removed from account {code}")


@account.command("by-tag")
@click.argument("tag")
@click.pass_context
@handle_error
def account_list_by_tag(ctx, tag):
    """List accounts that have a specific tag."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    accounts = ledger.list_accounts(tag=tag)

    if not accounts:
        console.print(f"[yellow]No accounts found with tag '{tag}'[/yellow]")
        return

    table = Table(title=f"Accounts tagged '{tag}'")
    table.add_column("Code", style="cyan")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Active")
    table.add_column("Tags")

    for a in accounts:
        table.add_row(
            a.code,
            a.name,
            a.account_type.value,
            "✓" if a.active else "✗",
            ", ".join(a.tags),
        )

    console.print(table)


# ── v0.7.0: Recurring Entries ──────────────────────────────────

@cli.group("recurring")
def recurring():
    """Manage recurring journal entries."""
    pass


@recurring.command("create")
@click.argument("name")
@click.argument("description")
@click.argument("lines", nargs=-1, required=True)
@click.option("--schedule", "-s", default="monthly",
              type=click.Choice(["daily", "weekly", "monthly", "quarterly", "yearly"]),
              help="Schedule type")
@click.option("--interval", "-i", type=int, default=1, help="Every N periods")
@click.option("--day-of-month", "-d", type=int, default=1, help="Day of month (1-31)")
@click.option("--day-of-week", "-w", type=int, default=0, help="Day of week (0=Mon)")
@click.option("--month-of-year", "-m", type=int, default=1, help="Month (1-12) for yearly")
@click.option("--start-date", default=None, help="Start date (ISO 8601)")
@click.option("--end-date", default=None, help="End date (ISO 8601)")
@click.option("--max-occurrences", type=int, default=None, help="Max number of entries")
@click.option("--tag", "-t", multiple=True, help="Tags for generated entries")
@click.pass_context
@handle_error
def recurring_create(ctx, name, description, lines, schedule, interval,
                     day_of_month, day_of_week, month_of_year,
                     start_date, end_date, max_occurrences, tag):
    """Create a recurring entry template.

    LINES are account:debit:credit triples, e.g. rent:2000:0 cash:0:2000
    """
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .recurring import RecurringManager

    # Parse lines: account:debit:credit
    line_dicts = []
    for l in lines:
        parts = l.split(":")
        if len(parts) != 3:
            click.echo(f"Error: line '{l}' must be account:debit:credit", err=True)
            sys.exit(1)
        line_dicts.append({
            "account_code": parts[0],
            "debit": float(parts[1]),
            "credit": float(parts[2]),
        })

    rm = RecurringManager(ledger)
    sd = _parse_cli_date(start_date)
    ed = _parse_cli_date(end_date)
    template = rm.create(
        name=name,
        description=description,
        lines=line_dicts,
        schedule_type=schedule,
        interval=interval,
        day_of_month=day_of_month,
        day_of_week=day_of_week,
        month_of_year=month_of_year,
        start_date=sd,
        end_date=ed,
        max_occurrences=max_occurrences,
        tags=list(tag) if tag else None,
    )
    console.print(f"[green]✓[/green] Created recurring template: {template.name}")
    console.print(f"  ID: {template.id}")
    console.print(f"  Schedule: {template.schedule_type.value} (interval={template.interval})")
    if template.next_run:
        console.print(f"  Next run: {template.next_run.strftime('%Y-%m-%d %H:%M')}")


@recurring.command("list")
@click.option("--active-only", is_flag=True, help="Show only active templates")
@click.pass_context
@handle_error
def recurring_list(ctx, active_only):
    """List recurring entry templates."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .recurring import RecurringManager, format_recurring_list
    rm = RecurringManager(ledger)
    templates = rm.list_templates(active_only=active_only)
    console.print(format_recurring_list(templates))


@recurring.command("show")
@click.argument("template_id")
@click.pass_context
@handle_error
def recurring_show(ctx, template_id):
    """Show details of a recurring template."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .recurring import RecurringManager, format_recurring_detail
    rm = RecurringManager(ledger)
    template = rm.get(template_id)
    console.print(format_recurring_detail(template))


@recurring.command("pause")
@click.argument("template_id")
@click.pass_context
@handle_error
def recurring_pause(ctx, template_id):
    """Pause a recurring template."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .recurring import RecurringManager
    rm = RecurringManager(ledger)
    rm.pause(template_id)
    console.print(f"[yellow]⏸[/yellow] Paused template {template_id}")


@recurring.command("resume")
@click.argument("template_id")
@click.pass_context
@handle_error
def recurring_resume(ctx, template_id):
    """Resume a paused recurring template."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .recurring import RecurringManager
    rm = RecurringManager(ledger)
    rm.resume(template_id)
    console.print(f"[green]▶[/green] Resumed template {template_id}")


@recurring.command("delete")
@click.argument("template_id")
@click.pass_context
@handle_error
def recurring_delete(ctx, template_id):
    """Delete a recurring template."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .recurring import RecurringManager
    rm = RecurringManager(ledger)
    rm.delete(template_id)
    console.print(f"[red]✗[/red] Deleted template {template_id}")


@recurring.command("process")
@click.option("--dry-run", is_flag=True, help="Show what would be generated without posting")
@click.pass_context
@handle_error
def recurring_process(ctx, dry_run):
    """Process all due recurring templates."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .recurring import RecurringManager
    rm = RecurringManager(ledger)

    if dry_run:
        due = [t for t in rm.list_templates(active_only=True) if rm.is_due(t)]
        if not due:
            console.print("[dim]No templates are due.[/dim]")
            return
        console.print(f"[cyan]{len(due)} template(s) due:[/cyan]")
        for t in due:
            console.print(f"  • {t.name} (next_run: {t.next_run.strftime('%Y-%m-%d') if t.next_run else 'N/A'})")
        return

    results = rm.process_all()
    generated = [r for r in results if r["status"] == "generated"]
    if generated:
        console.print(f"[green]✓[/green] Generated {len(generated)} entr(y/ies):")
        for r in generated:
            console.print(f"  • {r['template_name']} → entry {r['entry_id']}")
    else:
        console.print("[dim]No templates were due.[/dim]")


# ── v0.7.0: Financial Ratios ────────────────────────────────────

@report.command("ratios")
@click.option("--as-of", "-a", default=None, help="As-of date (ISO 8601)")
@click.pass_context
@handle_error
def report_ratios(ctx, as_of):
    """Show financial ratios and KPIs."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .ratios import compute_ratios, format_ratios
    as_of_dt = _parse_cli_date(as_of)
    ratios = compute_ratios(ledger, as_of=as_of_dt)
    console.print(format_ratios(ratios))


@report.command("health")
@click.option("--as-of", "-a", default=None, help="As-of date (ISO 8601)")
@click.pass_context
@handle_error
def report_health(ctx, as_of):
    """Show financial health assessment."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .ratios import compute_ratios, get_financial_health
    as_of_dt = _parse_cli_date(as_of)
    ratios = compute_ratios(ledger, as_of=as_of_dt)
    health = get_financial_health(ratios)

    console.print("[cyan]FINANCIAL HEALTH ASSESSMENT[/cyan]")
    console.print()

    status_colors = {
        "healthy": "green",
        "adequate": "yellow",
        "marginal": "yellow",
        "at_risk": "red",
    }
    status_icons = {
        "healthy": "✓",
        "adequate": "○",
        "marginal": "⚠",
        "at_risk": "✗",
    }

    if not health:
        console.print("[dim]Insufficient data for health assessment.[/dim]")
        return

    for category, info in health.items():
        status = info["status"]
        color = status_colors.get(status, "white")
        icon = status_icons.get(status, "?")
        console.print(f"  [{color}]{icon}[/{color}] {category.title()}: {status}")

    console.print()

    if ratios.warnings:
        console.print("[dim]Warnings:[/dim]")
        for w in ratios.warnings:
            console.print(f"  [yellow]⚠[/yellow] {w}")


# ── Aging Reports ────────────────────────────────────────────────

@report.command("aging")
@click.option("--accounts", "-a", required=True, help="Comma-separated account codes (e.g. 'ar' or 'ap')")
@click.option("--type", "report_type", "-t", type=click.Choice(["receivable", "payable"]), default="receivable", help="Report type")
@click.option("--as-of", "-d", default=None, help="As-of date (ISO 8601)")
@click.option("--details", is_flag=True, help="Show individual items")
@click.pass_context
@handle_error
def report_aging(ctx, accounts, report_type, as_of, details):
    """Show AR/AP aging report."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .aging import generate_aging_report, format_aging_report, AgingReportType
    as_of_dt = _parse_cli_date(as_of)
    codes = [c.strip() for c in accounts.split(",")]
    report = generate_aging_report(
        ledger, codes, AgingReportType(report_type), as_of=as_of_dt
    )
    console.print(format_aging_report(report, show_details=details))


# ── Depreciation / Fixed Assets ──────────────────────────────────

@cli.group("asset")
def asset():
    """Manage fixed assets and depreciation."""
    pass


@asset.command("create")
@click.option("--name", "-n", required=True, help="Asset name")
@click.option("--asset-account", required=True, help="Asset account code")
@click.option("--accum-account", required=True, help="Accumulated depreciation account code")
@click.option("--expense-account", required=True, help="Depreciation expense account code")
@click.option("--cost", "-c", type=float, required=True, help="Acquisition cost")
@click.option("--salvage", "-s", type=float, default=0.0, help="Salvage value")
@click.option("--life", "-l", type=int, required=True, help="Useful life in months")
@click.option("--method", "-m", type=click.Choice(["straight_line", "declining_balance", "double_declining", "units_of_production"]), default="straight_line")
@click.option("--rate", type=float, default=None, help="Custom declining balance rate")
@click.option("--total-units", type=float, default=None, help="Total units (for units-of-production)")
@click.option("--description", "-d", default="", help="Description")
@click.pass_context
@handle_error
def asset_create(ctx, name, asset_account, accum_account, expense_account, cost, salvage, life, method, rate, total_units, description):
    """Create a fixed asset for depreciation tracking."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .depreciation import DepreciationManager
    dm = DepreciationManager(ledger)
    asset = dm.create_asset(
        name=name,
        asset_account=asset_account,
        accum_dep_account=accum_account,
        dep_expense_account=expense_account,
        cost=cost,
        salvage_value=salvage,
        useful_life_months=life,
        method=method,
        declining_rate=rate,
        total_units=total_units,
        description=description,
    )
    console.print(f"[green]✓[/green] Created asset: {asset.name}")
    console.print(f"  ID: {asset.id}")
    console.print(f"  Cost: {asset.cost:,.2f}")
    console.print(f"  Book Value: {asset.book_value:,.2f}")


@asset.command("list")
@click.option("--active-only", is_flag=True, help="Show only active assets")
@click.pass_context
@handle_error
def asset_list(ctx, active_only):
    """List fixed assets."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .depreciation import DepreciationManager, format_asset_list
    dm = DepreciationManager(ledger)
    assets = dm.list_assets(active_only=active_only)
    console.print(format_asset_list(assets))


@asset.command("show")
@click.argument("asset_id")
@click.pass_context
@handle_error
def asset_show(ctx, asset_id):
    """Show details of a fixed asset."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .depreciation import DepreciationManager, format_asset_detail
    dm = DepreciationManager(ledger)
    asset = dm.get(asset_id)
    console.print(format_asset_detail(asset))


@asset.command("depreciate")
@click.argument("asset_id")
@click.pass_context
@handle_error
def asset_depreciate(ctx, asset_id):
    """Post one period of depreciation for an asset."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .depreciation import DepreciationManager
    dm = DepreciationManager(ledger)
    dep = dm.post_depreciation(asset_id)
    if dep:
        console.print(f"[green]✓[/green] Depreciation posted: {dep.amount:,.2f} ({dep.period})")
    else:
        console.print("[yellow]No depreciation posted (inactive or fully depreciated)[/yellow]")


@asset.command("depreciate-all")
@click.pass_context
@handle_error
def asset_depreciate_all(ctx):
    """Post depreciation for all active assets."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .depreciation import DepreciationManager
    dm = DepreciationManager(ledger)
    results = dm.post_all_depreciation()
    for r in results:
        status_color = "green" if r["status"] == "posted" else "yellow"
        console.print(
            f"  [{status_color}]{r['status']}[/{status_color}] "
            f"{r['asset_name']}: {r['amount']:,.2f}"
        )


@asset.command("schedule")
@click.argument("asset_id")
@click.option("--periods", "-p", type=int, default=None, help="Number of periods to project")
@click.pass_context
@handle_error
def asset_schedule(ctx, asset_id, periods):
    """Show projected depreciation schedule for an asset."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .depreciation import DepreciationManager, format_depreciation_schedule
    dm = DepreciationManager(ledger)
    asset_obj = dm.get(asset_id)
    schedule = dm.get_schedule(asset_id, periods=periods)
    console.print(format_depreciation_schedule(schedule, asset_obj.name))


@asset.command("dispose")
@click.argument("asset_id")
@click.option("--value", "-v", type=float, default=0.0, help="Disposal proceeds")
@click.option("--account", "-a", default=None, help="Account to debit for proceeds")
@click.pass_context
@handle_error
def asset_dispose(ctx, asset_id, value, account):
    """Dispose of a fixed asset."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .depreciation import DepreciationManager
    dm = DepreciationManager(ledger)
    result = dm.dispose(asset_id, disposal_value=value, disposal_account=account)
    gain_loss_color = "green" if result["gain"] else "red"
    gain_loss_word = "Gain" if result["gain"] else "Loss"
    console.print(f"[green]✓[/green] Disposed: {result['asset_name']}")
    console.print(f"  Book Value: {result['book_value']:,.2f}")
    console.print(f"  Disposal Value: {result['disposal_value']:,.2f}")
    console.print(f"  {gain_loss_word}: [{gain_loss_color}]{abs(result['gain_or_loss']):,.2f}[/{gain_loss_color}]")


@asset.command("delete")
@click.argument("asset_id")
@click.confirmation_option(prompt="Delete this asset record?")
@click.pass_context
@handle_error
def asset_delete(ctx, asset_id):
    """Delete a fixed asset record."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .depreciation import DepreciationManager
    dm = DepreciationManager(ledger)
    dm.delete(asset_id)
    console.print("[green]✓[/green] Asset deleted.")


# ── Serve Command (MCP) ─────────────────────────────────────────

@cli.command("serve")
@click.pass_context
@handle_error
def serve(ctx):
    """Start the MCP server for agent integration."""
    from .mcp_server import run_server
    console.print("[cyan]Starting Agent Ledger MCP server...[/cyan]")
    run_server(ledger_path=ctx.obj["ledger_file"])


if __name__ == "__main__":
    cli()
