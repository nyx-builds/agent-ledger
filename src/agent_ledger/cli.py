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


# ── Report Commands ─────────────────────────────────────────────

@cli.group("report")
def report():
    """Generate financial reports."""
    pass


@report.command("trial-balance")
@click.pass_context
@handle_error
def report_trial_balance(ctx):
    """Generate a trial balance."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    tb = generate_trial_balance(ledger)
    console.print(format_trial_balance(tb))


@report.command("income-statement")
@click.pass_context
@handle_error
def report_income_statement(ctx):
    """Generate an income statement."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    ist = generate_income_statement(ledger)
    console.print(format_income_statement(ist))


@report.command("balance-sheet")
@click.pass_context
@handle_error
def report_balance_sheet(ctx):
    """Generate a balance sheet."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    bs = generate_balance_sheet(ledger)
    console.print(format_balance_sheet(bs))


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


# ── Wallet Commands ──────────────────────────────────────────────

@cli.group("wallet")
def wallet():
    """Connect and sync Solana wallet transactions."""
    pass


@wallet.command("connect")
@click.argument("address")
@click.option("--network", "-n", default="mainnet",
              type=click.Choice(["mainnet", "devnet"]),
              help="Solana network")
@click.pass_context
@handle_error
def wallet_connect(ctx, address, network):
    """Connect a Solana wallet and check balance."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .importer import WalletImporter
    importer = WalletImporter(ledger=ledger)
    try:
        info = importer.connect_wallet(address, network=network)
        console.print(Panel(
            f"[cyan]Address:[/cyan] {info.address}\n"
            f"[cyan]Network:[/cyan] {info.network}\n"
            f"[cyan]Balance:[/cyan] {info.sol_balance:.6f} SOL\n"
            f"[cyan]Transactions Imported:[/cyan] {info.transaction_count}\n"
            f"[cyan]Last Synced:[/cyan] {info.last_synced_at.strftime('%Y-%m-%d %H:%M UTC') if info.last_synced_at else 'Never'}",
            title="Wallet Connected",
        ))
    except Exception as e:
        console.print(f"[red]Failed to connect wallet:[/red] {e}")
    finally:
        importer.close()


@wallet.command("sync")
@click.argument("address")
@click.option("--network", "-n", default="mainnet",
              type=click.Choice(["mainnet", "devnet"]),
              help="Solana network")
@click.option("--limit", "-l", default=50, help="Max transactions to fetch")
@click.option("--no-accounts", is_flag=True, help="Don't auto-create accounts")
@click.option("--no-fees", is_flag=True, help="Don't create separate fee entries")
@click.option("--dry-run", is_flag=True, help="Categorize without posting")
@click.pass_context
@handle_error
def wallet_sync(ctx, address, network, limit, no_accounts, no_fees, dry_run):
    """Sync wallet transactions into the ledger as journal entries."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .importer import WalletImporter
    importer = WalletImporter(ledger=ledger)
    try:
        result = importer.sync_wallet(
            wallet_address=address,
            network=network,
            limit=limit,
            create_accounts=not no_accounts,
            import_fees=not no_fees,
            dry_run=dry_run,
        )

        if dry_run:
            console.print("[yellow]DRY RUN — no entries were posted[/yellow]")

        console.print(Panel(
            f"[cyan]Wallet:[/cyan] {result.wallet_address}\n"
            f"[cyan]Fetched:[/cyan] {result.transactions_fetched}\n"
            f"[green]Imported:[/green] {result.transactions_imported}\n"
            f"[yellow]Skipped:[/yellow] {result.transactions_skipped}\n"
            f"[red]Failed:[/red] {result.transactions_failed}\n"
            f"[cyan]Total SOL:[/cyan] {result.total_sol_imported:.6f}",
            title="Wallet Sync Results",
        ))

        if result.errors:
            console.print("\n[red]Errors:[/red]")
            for err in result.errors[:5]:
                console.print(f"  • {err}")

    except Exception as e:
        console.print(f"[red]Sync failed:[/red] {e}")
    finally:
        importer.close()


@wallet.command("status")
@click.argument("address")
@click.option("--network", "-n", default="mainnet",
              type=click.Choice(["mainnet", "devnet"]),
              help="Solana network")
@click.pass_context
@handle_error
def wallet_status(ctx, address, network):
    """Show wallet sync status and balance."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .importer import WalletImporter
    importer = WalletImporter(ledger=ledger)
    try:
        info = importer.get_wallet_info(address, network=network)
        console.print(Panel(
            f"[cyan]Address:[/cyan] {info.address}\n"
            f"[cyan]Network:[/cyan] {info.network}\n"
            f"[cyan]Balance:[/cyan] {info.sol_balance:.6f} SOL ({info.lamports:,} lamports)\n"
            f"[cyan]Last Synced Slot:[/cyan] {info.last_synced_slot or 'Never'}\n"
            f"[cyan]Last Synced At:[/cyan] {info.last_synced_at.strftime('%Y-%m-%d %H:%M UTC') if info.last_synced_at else 'Never'}\n"
            f"[cyan]Transactions Imported:[/cyan] {info.transaction_count}",
            title="Wallet Status",
        ))
    except Exception as e:
        console.print(f"[red]Failed to get wallet status:[/red] {e}")
    finally:
        importer.close()


@wallet.command("setup-accounts")
@click.pass_context
@handle_error
def wallet_setup_accounts(ctx):
    """Create the default chart of accounts for Solana wallet tracking."""
    ledger = get_ledger(ctx.obj["ledger_file"])
    from .importer import WalletImporter
    importer = WalletImporter(ledger=ledger)
    try:
        created = importer.setup_wallet_accounts()
        if created:
            console.print(f"[green]✓[/green] Created {len(created)} accounts:")
            for code in created:
                console.print(f"  • {code}")
        else:
            console.print("[yellow]All wallet accounts already exist[/yellow]")
    finally:
        importer.close()


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
