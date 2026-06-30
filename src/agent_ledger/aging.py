"""Aging reports for agent-ledger.

Generate Accounts Receivable (AR) and Accounts Payable (AP) aging reports.
These show outstanding balances bucketed by how long they have been unpaid —
a critical tool for any agent managing receivables or payables.

How it works
------------
The aging engine inspects every journal entry that touches a target set of
accounts (typically AR or AP) and uses a **transaction-level running balance**
to determine how much of the current outstanding balance originates from
unpaid entries in each time bucket.

For each entry affecting the target account, the engine computes:

* The **net effect** of that entry on the outstanding balance (increase /
  decrease).  Increases add to the entry's "open" amount; decreases
  (payments / credits) are applied oldest-first (FIFO) to reduce open
  amounts from earlier entries.

* The **open amount** that remains unpaid as of the report date.

* The **age** of each open amount, measured from the entry date to the
  report date.

Buckets
-------
Default buckets: 0-30, 31-60, 61-90, 90+ days.  Fully configurable.

Example::

    from agent_ledger.aging import generate_aging_report, AgingReportType

    report = generate_aging_report(
        ledger,
        account_codes=["accounts_receivable"],
        report_type=AgingReportType.RECEIVABLE,
    )
    print(format_aging_report(report))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from .ledger import Ledger


class AgingError(Exception):
    """Error related to aging report generation."""


class AgingReportType(str, Enum):
    """Type of aging report."""
    RECEIVABLE = "receivable"
    PAYABLE = "payable"


# ── Data structures ──────────────────────────────────────────────


@dataclass
class AgingItem:
    """A single open (unpaid) entry in an aging report.

    Attributes:
        entry_id: The journal entry id.
        entry_date: Date of the original entry.
        description: Entry description.
        days_outstanding: Age in days from entry_date to report date.
        open_amount: Remaining unpaid amount.
        bucket: The bucket label this item falls into.
    """
    entry_id: str
    entry_date: datetime
    description: str
    days_outstanding: int
    open_amount: float
    bucket: str


@dataclass
class AgingBucket:
    """A time bucket in the aging report.

    Attributes:
        label: Human-readable label (e.g. "0-30").
        min_days: Minimum days (inclusive).
        max_days: Maximum days (inclusive), or None for the last bucket.
        total: Total open amount in this bucket.
        items: List of items in this bucket.
    """
    label: str
    min_days: int
    max_days: Optional[int]
    total: float = 0.0
    items: list[AgingItem] = field(default_factory=list)


@dataclass
class AgingReport:
    """Full aging report.

    Attributes:
        report_type: Receivable or payable.
        account_codes: Accounts included in the report.
        as_of: Report date.
        total_outstanding: Sum of all open amounts.
        buckets: Ordered list of aging buckets.
        warnings: List of warnings.
    """
    report_type: AgingReportType
    account_codes: list[str]
    as_of: datetime
    total_outstanding: float = 0.0
    buckets: list[AgingBucket] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Core computation ─────────────────────────────────────────────


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _default_buckets() -> list[tuple[str, int, Optional[int]]]:
    """Return default bucket definitions: (label, min_days, max_days)."""
    return [
        ("0-30", 0, 30),
        ("31-60", 31, 60),
        ("61-90", 61, 90),
        ("90+", 91, None),
    ]


def _bucket_for_days(days: int, bucket_defs: list[tuple[str, int, Optional[int]]]) -> str:
    """Find the bucket label for a given number of days."""
    for label, min_d, max_d in bucket_defs:
        if max_d is None:
            if days >= min_d:
                return label
        elif min_d <= days <= max_d:
            return label
    # Fallback: last bucket
    return bucket_defs[-1][0]


def generate_aging_report(
    ledger: Ledger,
    account_codes: list[str],
    report_type: AgingReportType = AgingReportType.RECEIVABLE,
    as_of: Optional[datetime] = None,
    bucket_defs: Optional[list[tuple[str, int, Optional[int]]]] = None,
) -> AgingReport:
    """Generate an AR or AP aging report.

    This function analyses every entry touching the specified account(s) and
    computes the outstanding (unpaid) amount from each entry, bucketed by age.

    For **receivables** (AR), an entry increases the outstanding amount when it
    debits the AR account (a sale on credit) and decreases it when it credits
    the AR account (payment received).

    For **payables** (AP), an entry increases the outstanding amount when it
    credits the AP account (a purchase on credit) and decreases it when it
    debits the AP account (payment made).

    Payments are applied FIFO — they reduce the oldest open entries first.

    Args:
        ledger: The ledger to analyse.
        account_codes: AR or AP account codes to include.
        report_type: Whether this is a receivable or payable report.
        as_of: Report date (default: now).
        bucket_defs: Custom bucket definitions as (label, min_days, max_days).
                     Default: [("0-30", 0, 30), ("31-60", 31, 60),
                               ("61-90", 61, 90), ("90+", 91, None)]

    Returns:
        An AgingReport with bucketed open amounts.

    Raises:
        AgingError: If no account codes provided or accounts don't exist.
    """
    if not account_codes:
        raise AgingError("At least one account code is required")

    as_of = _ensure_aware(as_of) if as_of else datetime.now(timezone.utc)
    bucket_defs = bucket_defs or _default_buckets()

    # Normalise codes and validate
    codes = []
    for code in account_codes:
        c = code.strip().lower()
        ledger.get_account(c)  # raises AccountNotFoundError if missing
        codes.append(c)

    # For receivables, debits increase the outstanding amount.
    # For payables, credits increase the outstanding amount.
    if report_type == AgingReportType.RECEIVABLE:
        increase_side = "debit"
    else:
        increase_side = "credit"

    # Collect all relevant entries in chronological order, up to as_of
    relevant_entries = []
    for entry in sorted(ledger.data.entries, key=lambda e: e.timestamp):
        entry_date = _ensure_aware(entry.timestamp)
        if entry_date > as_of:
            continue  # Entry is after the report date — skip
        has_relevant_line = False
        net_increase = 0.0
        for line in entry.lines:
            if line.account_code in codes:
                has_relevant_line = True
                if increase_side == "debit":
                    net_increase += line.debit - line.credit
                else:
                    net_increase += line.credit - line.debit

        if has_relevant_line and abs(net_increase) > 0.001:
            relevant_entries.append((entry, round(net_increase, 2)))

    # Apply FIFO: entries with positive net_increase create open amounts.
    # Entries with negative net_increase (payments) reduce oldest open amounts.
    open_entries: list[dict] = []  # list of {entry, date, open}

    for entry, net in relevant_entries:
        if net > 0:
            # New receivable/payable created
            open_entries.append({
                "entry": entry,
                "open": net,
            })
        elif net < 0:
            # Payment — apply FIFO to reduce open amounts
            remaining_payment = abs(net)
            for oe in open_entries:
                if remaining_payment <= 0.001:
                    break
                if oe["open"] > 0:
                    applied = min(oe["open"], remaining_payment)
                    oe["open"] = round(oe["open"] - applied, 2)
                    remaining_payment = round(remaining_payment - applied, 2)

    # Filter out fully paid entries
    open_entries = [oe for oe in open_entries if oe["open"] > 0.005]

    # Build report
    report = AgingReport(
        report_type=report_type,
        account_codes=codes,
        as_of=as_of,
    )

    # Initialise buckets
    bucket_map: dict[str, AgingBucket] = {}
    for label, min_d, max_d in bucket_defs:
        b = AgingBucket(label=label, min_days=min_d, max_days=max_d)
        report.buckets.append(b)
        bucket_map[label] = b

    # Assign open entries to buckets
    for oe in open_entries:
        entry = oe["entry"]
        open_amount = round(oe["open"], 2)
        entry_date = _ensure_aware(entry.timestamp)
        days = (as_of - entry_date).days
        days = max(0, days)

        label = _bucket_for_days(days, bucket_defs)
        item = AgingItem(
            entry_id=entry.id,
            entry_date=entry.timestamp,
            description=entry.description,
            days_outstanding=days,
            open_amount=open_amount,
            bucket=label,
        )
        bucket_map[label].items.append(item)
        bucket_map[label].total = round(bucket_map[label].total + open_amount, 2)
        report.total_outstanding = round(report.total_outstanding + open_amount, 2)

    # Sort items within each bucket by days outstanding (oldest first)
    for b in report.buckets:
        b.items.sort(key=lambda i: -i.days_outstanding)

    # Warnings
    if report.total_outstanding == 0:
        report.warnings.append("No outstanding balance found.")
    # Check for very old items (> 180 days)
    very_old = sum(1 for b in report.buckets if b.min_days >= 90 for _ in b.items)
    if very_old > 0:
        report.warnings.append(f"{very_old} item(s) older than 90 days — consider write-off or follow-up.")

    return report


# ── Formatting ───────────────────────────────────────────────────


def format_aging_report(report: AgingReport, show_details: bool = False) -> str:
    """Format an aging report as a human-readable text report.

    Args:
        report: The aging report to format.
        show_details: If True, list individual items under each bucket.
    """
    type_label = "ACCOUNTS RECEIVABLE" if report.report_type == AgingReportType.RECEIVABLE else "ACCOUNTS PAYABLE"
    lines = []
    lines.append(f"{type_label} AGING REPORT")
    lines.append(f"As of: {report.as_of.strftime('%Y-%m-%d')}")
    lines.append(f"Accounts: {', '.join(report.account_codes)}")
    lines.append("")

    # Summary table
    header = f"  {'Bucket':<12}"
    for b in report.buckets:
        header += f" {b.label:>12}"
    header += f" {'Total':>14}"
    lines.append(header)
    lines.append(f"  {'-' * (len(header) - 2)}")

    total_row = f"  {'Amount':<12}"
    for b in report.buckets:
        total_row += f" {b.total:>12,.2f}"
    total_row += f" {report.total_outstanding:>14,.2f}"
    lines.append(total_row)

    pct_row = f"  {'% of Total':<12}"
    for b in report.buckets:
        if report.total_outstanding > 0:
            pct = (b.total / report.total_outstanding) * 100
            pct_row += f" {pct:>11.1f}%"
        else:
            pct_row += f" {'N/A':>12}"
    pct_row += f" {'100.0%':>14}"
    lines.append(pct_row)

    # Detail items
    if show_details and report.total_outstanding > 0:
        lines.append("")
        lines.append("DETAILS BY BUCKET")
        lines.append("")
        for b in report.buckets:
            if not b.items:
                continue
            lines.append(f"  {b.label} days (Total: {b.total:,.2f})")
            lines.append(f"    {'Date':<12} {'Days':>5} {'Amount':>14}  Description")
            lines.append(f"    {'-' * 60}")
            for item in b.items:
                date_str = item.entry_date.strftime("%Y-%m-%d") if item.entry_date else "N/A"
                lines.append(
                    f"    {date_str:<12} {item.days_outstanding:>5} {item.open_amount:>14,.2f}  {item.description}"
                )
            lines.append("")

    if report.warnings:
        lines.append("")
        lines.append("WARNINGS")
        for w in report.warnings:
            lines.append(f"  ⚠ {w}")

    return "\n".join(lines)


def aging_summary_dict(report: AgingReport) -> dict:
    """Return a machine-readable summary of an aging report.

    Useful for MCP tool responses or programmatic consumption.
    """
    return {
        "report_type": report.report_type.value,
        "as_of": report.as_of.isoformat(),
        "account_codes": report.account_codes,
        "total_outstanding": report.total_outstanding,
        "buckets": [
            {
                "label": b.label,
                "min_days": b.min_days,
                "max_days": b.max_days,
                "total": b.total,
                "item_count": len(b.items),
            }
            for b in report.buckets
        ],
        "item_count": sum(len(b.items) for b in report.buckets),
        "warnings": report.warnings,
    }
