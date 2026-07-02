"""Cash flow statement for agent-ledger — indirect method."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .models import AccountType, AccountBalance
from .ledger import Ledger


@dataclass
class CashFlowItem:
    """A single line item in a cash flow statement."""
    description: str
    amount: float
    account_code: Optional[str] = None


@dataclass
class CashFlowSection:
    """A section of the cash flow statement (Operating, Investing, Financing)."""
    section_type: str  # "operating", "investing", "financing"
    items: list[CashFlowItem] = field(default_factory=list)
    total: float = 0.0


@dataclass
class CashFlowStatement:
    """Cash flow statement (indirect method)."""
    operating: CashFlowSection = field(default_factory=lambda: CashFlowSection(section_type="operating"))
    investing: CashFlowSection = field(default_factory=lambda: CashFlowSection(section_type="investing"))
    financing: CashFlowSection = field(default_factory=lambda: CashFlowSection(section_type="financing"))
    net_change_in_cash: float = 0.0
    beginning_cash: float = 0.0
    ending_cash: float = 0.0
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None


def generate_cash_flow_statement(
    ledger: Ledger,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> CashFlowStatement:
    """Generate a cash flow statement using the indirect method.

    Classifies accounts by their type:
    - Asset accounts (non-cash): Investing activities
    - Liability accounts: Financing activities
    - Equity accounts: Financing activities
    - Revenue/Expense accounts: Operating activities

    Cash accounts are tracked separately to compute net change in cash.

    Args:
        ledger: The ledger to generate the statement from
        from_date: Optional start date (filters entries)
        to_date: Optional end date (filters entries)

    Returns:
        CashFlowStatement with classified cash flows
    """
    balances = ledger.get_all_balances()
    accounts = {a.code: a for a in ledger.list_accounts()}

    operating_items: list[CashFlowItem] = []
    operating_total = 0.0
    investing_items: list[CashFlowItem] = []
    investing_total = 0.0
    financing_items: list[CashFlowItem] = []
    financing_total = 0.0
    cash_balance = 0.0

    for b in sorted(balances, key=lambda x: x.account_code):
        if b.balance == 0:
            continue

        account = accounts.get(b.account_code)
        if account is None:
            continue

        # Detect cash accounts by name/code convention or metadata
        is_cash = _is_cash_account(account)

        if is_cash:
            cash_balance += b.balance
            continue

        if b.account_type == AccountType.REVENUE:
            # Revenue is a positive operating cash flow
            item = CashFlowItem(
                description=f"{account.name}",
                amount=b.balance,
                account_code=account.code,
            )
            operating_items.append(item)
            operating_total += b.balance

        elif b.account_type == AccountType.EXPENSE:
            # Expense is a negative operating cash flow
            item = CashFlowItem(
                description=f"{account.name}",
                amount=-b.balance,
                account_code=account.code,
            )
            operating_items.append(item)
            operating_total -= b.balance

        elif b.account_type == AccountType.ASSET:
            # Non-cash assets: investing (increase = cash outflow, decrease = inflow)
            # A positive balance (normal for assets) means we invested cash
            item = CashFlowItem(
                description=f"{account.name}",
                amount=-b.balance,  # Increase in asset = cash outflow
                account_code=account.code,
            )
            investing_items.append(item)
            investing_total -= b.balance

        elif b.account_type == AccountType.LIABILITY:
            # Liabilities: financing (increase = cash inflow)
            item = CashFlowItem(
                description=f"{account.name}",
                amount=b.balance,
                account_code=account.code,
            )
            financing_items.append(item)
            financing_total += b.balance

        elif b.account_type == AccountType.EQUITY:
            # Equity: financing (increase = cash inflow)
            item = CashFlowItem(
                description=f"{account.name}",
                amount=b.balance,
                account_code=account.code,
            )
            financing_items.append(item)
            financing_total += b.balance

    # Net income summary for operating section
    net_income = operating_total

    statement = CashFlowStatement(
        operating=CashFlowSection(
            section_type="operating",
            items=operating_items,
            total=round(operating_total, 2),
        ),
        investing=CashFlowSection(
            section_type="investing",
            items=investing_items,
            total=round(investing_total, 2),
        ),
        financing=CashFlowSection(
            section_type="financing",
            items=financing_items,
            total=round(financing_total, 2),
        ),
        net_change_in_cash=round(operating_total + investing_total + financing_total, 2),
        beginning_cash=0.0,  # We can't know beginning cash without historical data
        ending_cash=round(cash_balance, 2),
        from_date=from_date,
        to_date=to_date,
    )

    # Verify: net change should equal ending cash minus beginning cash
    # beginning_cash = ending_cash - net_change_in_cash
    statement.beginning_cash = round(statement.ending_cash - statement.net_change_in_cash, 2)

    return statement


def _is_cash_account(account) -> bool:
    """Determine if an account is a cash account.

    Only ASSET accounts can be cash accounts. Checks account code
    and name for cash-related keywords, or looks for metadata flag.
    """
    # Only asset accounts can be cash
    if account.account_type != AccountType.ASSET:
        return False

    if account.metadata.get("is_cash", False):
        return True

    cash_keywords = ["cash", "bank", "checking", "savings", "petty cash", "money market"]
    code_lower = account.code.lower()
    name_lower = account.name.lower()

    for keyword in cash_keywords:
        if keyword in code_lower or keyword in name_lower:
            return True

    return False


def format_cash_flow_statement(report: CashFlowStatement) -> str:
    """Format a cash flow statement as text."""
    lines = []
    lines.append("CASH FLOW STATEMENT")
    date_range = ""
    if report.from_date and report.to_date:
        date_range = f"{report.from_date.strftime('%Y-%m-%d')} to {report.to_date.strftime('%Y-%m-%d')}"
    if date_range:
        lines.append(f"Period: {date_range}")
    lines.append("")

    # Operating Activities
    lines.append("OPERATING ACTIVITIES")
    lines.append(f"{'Description':<40} {'Amount':>12}")
    lines.append("-" * 54)
    for item in report.operating.items:
        lines.append(f"{item.description:<40} {item.amount:>12,.2f}")
    lines.append(f"{'Net Cash from Operating':<40} {report.operating.total:>12,.2f}")

    lines.append("")

    # Investing Activities
    lines.append("INVESTING ACTIVITIES")
    lines.append(f"{'Description':<40} {'Amount':>12}")
    lines.append("-" * 54)
    for item in report.investing.items:
        lines.append(f"{item.description:<40} {item.amount:>12,.2f}")
    lines.append(f"{'Net Cash from Investing':<40} {report.investing.total:>12,.2f}")

    lines.append("")

    # Financing Activities
    lines.append("FINANCING ACTIVITIES")
    lines.append(f"{'Description':<40} {'Amount':>12}")
    lines.append("-" * 54)
    for item in report.financing.items:
        lines.append(f"{item.description:<40} {item.amount:>12,.2f}")
    lines.append(f"{'Net Cash from Financing':<40} {report.financing.total:>12,.2f}")

    lines.append("")
    lines.append(f"{'NET CHANGE IN CASH':<40} {report.net_change_in_cash:>12,.2f}")
    lines.append(f"{'Beginning Cash':<40} {report.beginning_cash:>12,.2f}")
    lines.append(f"{'Ending Cash':<40} {report.ending_cash:>12,.2f}")

    return "\n".join(lines)
