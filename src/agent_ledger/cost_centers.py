"""Cost centers / profit centers / projects for agent-ledger.

Provides dimensional accounting so agents can track revenue and expenses
by project, department, cost center, or any other business dimension.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from .models import AccountType, JournalEntry
from .ledger import Ledger
from .reports import _ensure_aware


# ── Data Models ───────────────────────────────────────────────

@dataclass
class CostCenter:
    """A cost center, profit center, or project for dimensional accounting."""
    code: str
    name: str
    center_type: str  # "cost", "profit", "project", "department", "investment"
    description: str = ""
    parent_code: Optional[str] = None
    active: bool = True
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "center_type": self.center_type,
            "description": self.description,
            "parent_code": self.parent_code,
            "active": self.active,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CostCenter:
        return cls(
            code=d["code"],
            name=d["name"],
            center_type=d.get("center_type", "cost"),
            description=d.get("description", ""),
            parent_code=d.get("parent_code"),
            active=d.get("active", True),
            tags=d.get("tags", []),
            metadata=d.get("metadata", {}),
            created_at=datetime.fromisoformat(d["created_at"]) if isinstance(d.get("created_at"), str) else d.get("created_at", datetime.now(timezone.utc)),
        )


@dataclass
class CostCenterLine:
    """A single account line within a cost center report."""
    account_code: str
    account_name: str
    account_type: AccountType
    debit: float
    credit: float
    balance: float


@dataclass
class CostCenterReport:
    """Financial report for a specific cost center."""
    cost_center: Optional[CostCenter]
    lines: list[CostCenterLine] = field(default_factory=list)
    total_revenue: float = 0.0
    total_expenses: float = 0.0
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    net_income: float = 0.0
    entry_count: int = 0
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None

    def to_dict(self) -> dict:
        cc = self.cost_center.to_dict() if self.cost_center else None
        return {
            "cost_center": cc,
            "lines": [
                {
                    "account_code": l.account_code,
                    "account_name": l.account_name,
                    "account_type": l.account_type.value,
                    "debit": l.debit,
                    "credit": l.credit,
                    "balance": l.balance,
                }
                for l in self.lines
            ],
            "totals": {
                "revenue": self.total_revenue,
                "expenses": self.total_expenses,
                "assets": self.total_assets,
                "liabilities": self.total_liabilities,
                "net_income": self.net_income,
            },
            "entry_count": self.entry_count,
            "from_date": self.from_date.isoformat() if self.from_date else None,
            "to_date": self.to_date.isoformat() if self.to_date else None,
        }


@dataclass
class CostCenterSummary:
    """Summary of all cost centers with key metrics."""
    centers: list[dict] = field(default_factory=list)
    total_revenue: float = 0.0
    total_expenses: float = 0.0
    total_net_income: float = 0.0
    unassigned_revenue: float = 0.0
    unassigned_expenses: float = 0.0

    def to_dict(self) -> dict:
        return {
            "centers": self.centers,
            "totals": {
                "revenue": self.total_revenue,
                "expenses": self.total_expenses,
                "net_income": self.total_net_income,
                "unassigned_revenue": self.unassigned_revenue,
                "unassigned_expenses": self.unassigned_expenses,
            },
        }


# ── Cost Center Manager ──────────────────────────────────────

class CostCenterManager:
    """Manage cost centers and generate dimensional reports.

    Cost centers are stored in the ledger's metadata under the key
    ``cost_centers``. Journal entries are linked to cost centers via
    their ``metadata.cost_center`` field.
    """

    METADATA_KEY = "cost_centers"

    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    # ── CRUD ─────────────────────────────────────────────────

    def _store(self) -> list[dict]:
        """Get the raw cost center list from ledger metadata."""
        return self.ledger.data.metadata.setdefault(self.METADATA_KEY, [])

    def _save(self) -> None:
        self.ledger.save()

    def create(
        self,
        code: str,
        name: str,
        center_type: str = "cost",
        description: str = "",
        parent_code: Optional[str] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> CostCenter:
        """Create a new cost center.

        Args:
            code: Unique code (e.g. 'proj-alpha', 'dept-eng')
            name: Human-readable name
            center_type: One of 'cost', 'profit', 'project', 'department', 'investment'
            description: Optional description
            parent_code: Optional parent cost center code for hierarchy
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            The created CostCenter

        Raises:
            ValueError: If code already exists or parent not found
        """
        code = code.strip().lower()

        valid_types = {"cost", "profit", "project", "department", "investment"}
        if center_type not in valid_types:
            raise ValueError(
                f"Invalid center_type '{center_type}'. Must be one of: {sorted(valid_types)}"
            )

        store = self._store()
        existing_codes = {c["code"] for c in store}
        if code in existing_codes:
            raise ValueError(f"Cost center '{code}' already exists")

        if parent_code:
            parent_code = parent_code.strip().lower()
            if parent_code not in existing_codes:
                raise ValueError(f"Parent cost center '{parent_code}' not found")

        cc = CostCenter(
            code=code,
            name=name,
            center_type=center_type,
            description=description,
            parent_code=parent_code,
            tags=tags or [],
            metadata=metadata or {},
        )
        store.append(cc.to_dict())
        self._save()
        return cc

    def get(self, code: str) -> CostCenter:
        """Get a cost center by code."""
        code = code.strip().lower()
        for d in self._store():
            if d["code"] == code:
                return CostCenter.from_dict(d)
        raise ValueError(f"Cost center '{code}' not found")

    def list(self, active_only: bool = False, center_type: Optional[str] = None) -> list[CostCenter]:
        """List all cost centers, optionally filtered."""
        results = [CostCenter.from_dict(d) for d in self._store()]
        if active_only:
            results = [c for c in results if c.active]
        if center_type:
            results = [c for c in results if c.center_type == center_type]
        return sorted(results, key=lambda c: c.code)

    def update(
        self,
        code: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        active: Optional[bool] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> CostCenter:
        """Update an existing cost center."""
        code = code.strip().lower()
        store = self._store()
        for i, d in enumerate(store):
            if d["code"] == code:
                if name is not None:
                    d["name"] = name
                if description is not None:
                    d["description"] = description
                if active is not None:
                    d["active"] = active
                if tags is not None:
                    d["tags"] = tags
                if metadata is not None:
                    d.setdefault("metadata", {}).update(metadata)
                store[i] = d
                self._save()
                return CostCenter.from_dict(d)
        raise ValueError(f"Cost center '{code}' not found")

    def delete(self, code: str) -> None:
        """Delete a cost center. Fails if it has children or entries assigned."""
        code = code.strip().lower()
        store = self._store()

        # Check children
        children = [c for c in store if c.get("parent_code") == code]
        if children:
            child_codes = [c["code"] for c in children]
            raise ValueError(f"Cannot delete '{code}' with child cost centers: {child_codes}")

        # Check for assigned entries
        assigned = self._entries_for_cost_center(code)
        if assigned:
            raise ValueError(
                f"Cannot delete '{code}' — {len(assigned)} entries are assigned to it. "
                "Reassign or delete those entries first."
            )

        store[:] = [c for c in store if c["code"] != code]
        self._save()

    # ── Entry Assignment ─────────────────────────────────────

    def assign_entry(self, entry_id: str, cost_center_code: str) -> JournalEntry:
        """Assign a journal entry to a cost center.

        Args:
            entry_id: The journal entry ID
            cost_center_code: The cost center code

        Returns:
            The updated JournalEntry
        """
        # Validate cost center exists
        self.get(cost_center_code)
        entry = self.ledger.get_entry(entry_id)
        entry.metadata["cost_center"] = cost_center_code.strip().lower()
        self.ledger.save()
        return entry

    def unassign_entry(self, entry_id: str) -> JournalEntry:
        """Remove cost center assignment from a journal entry."""
        entry = self.ledger.get_entry(entry_id)
        entry.metadata.pop("cost_center", None)
        self.ledger.save()
        return entry

    def _entries_for_cost_center(self, code: str) -> list[JournalEntry]:
        """Get all entries assigned to a cost center."""
        code = code.strip().lower()
        return [
            e for e in self.ledger.data.entries
            if e.metadata.get("cost_center") == code
        ]

    def list_entries(
        self,
        cost_center_code: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list[JournalEntry]:
        """List all entries for a cost center with optional date filtering."""
        entries = self._entries_for_cost_center(cost_center_code)
        if start_date is not None:
            start_aware = _ensure_aware(start_date)
            entries = [e for e in entries if _ensure_aware(e.timestamp) >= start_aware]
        if end_date is not None:
            end_aware = _ensure_aware(end_date)
            entries = [e for e in entries if _ensure_aware(e.timestamp) <= end_aware]
        return sorted(entries, key=lambda e: e.timestamp)

    # ── Reporting ────────────────────────────────────────────

    def report(
        self,
        cost_center_code: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> CostCenterReport:
        """Generate a financial report for a specific cost center.

        Shows all account activity for entries assigned to this cost center,
        broken down by account with totals for revenue, expenses, assets, etc.
        """
        cc = self.get(cost_center_code)
        entries = self.list_entries(cost_center_code, from_date, to_date)

        accounts = {a.code: a for a in self.ledger.list_accounts()}
        debit_totals: dict[str, float] = {}
        credit_totals: dict[str, float] = {}

        for entry in entries:
            for line in entry.lines:
                debit_totals[line.account_code] = debit_totals.get(line.account_code, 0.0) + line.debit
                credit_totals[line.account_code] = credit_totals.get(line.account_code, 0.0) + line.credit

        lines: list[CostCenterLine] = []
        total_revenue = 0.0
        total_expenses = 0.0
        total_assets = 0.0
        total_liabilities = 0.0

        for code in sorted(debit_totals.keys() | credit_totals.keys()):
            acct = accounts.get(code)
            if acct is None:
                continue
            dt = round(debit_totals.get(code, 0.0), 2)
            ct = round(credit_totals.get(code, 0.0), 2)

            # Normal balance
            if acct.account_type.is_debit_account:
                bal = round(dt - ct, 2)
            else:
                bal = round(ct - dt, 2)

            lines.append(CostCenterLine(
                account_code=code,
                account_name=acct.name,
                account_type=acct.account_type,
                debit=dt,
                credit=ct,
                balance=bal,
            ))

            if acct.account_type == AccountType.REVENUE:
                total_revenue += bal
            elif acct.account_type == AccountType.EXPENSE:
                total_expenses += bal
            elif acct.account_type == AccountType.ASSET:
                total_assets += bal
            elif acct.account_type == AccountType.LIABILITY:
                total_liabilities += bal

        return CostCenterReport(
            cost_center=cc,
            lines=lines,
            total_revenue=round(total_revenue, 2),
            total_expenses=round(total_expenses, 2),
            total_assets=round(total_assets, 2),
            total_liabilities=round(total_liabilities, 2),
            net_income=round(total_revenue - total_expenses, 2),
            entry_count=len(entries),
            from_date=from_date,
            to_date=to_date,
        )

    def summary(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> CostCenterSummary:
        """Generate a summary of all cost centers with key metrics.

        Shows revenue, expenses, and net income for each cost center,
        plus totals for unassigned entries.
        """
        centers = self.list()
        center_summaries: list[dict] = []
        total_rev = 0.0
        total_exp = 0.0
        total_ni = 0.0

        for cc in centers:
            r = self.report(cc.code, from_date, to_date)
            center_summaries.append({
                "code": cc.code,
                "name": cc.name,
                "center_type": cc.center_type,
                "active": cc.active,
                "revenue": r.total_revenue,
                "expenses": r.total_expenses,
                "net_income": r.net_income,
                "entry_count": r.entry_count,
            })
            total_rev += r.total_revenue
            total_exp += r.total_expenses
            total_ni += r.net_income

        # Calculate unassigned entries
        unassigned_rev = 0.0
        unassigned_exp = 0.0
        accounts = {a.code: a for a in self.ledger.list_accounts()}

        for entry in self.ledger.data.entries:
            if entry.metadata.get("cost_center"):
                continue
            ts = _ensure_aware(entry.timestamp)
            if from_date and ts < _ensure_aware(from_date):
                continue
            if to_date and ts > _ensure_aware(to_date):
                continue
            for line in entry.lines:
                acct = accounts.get(line.account_code)
                if acct is None:
                    continue
                if acct.account_type == AccountType.REVENUE:
                    unassigned_rev += line.credit - line.debit
                elif acct.account_type == AccountType.EXPENSE:
                    unassigned_exp += line.debit - line.credit

        return CostCenterSummary(
            centers=center_summaries,
            total_revenue=round(total_rev, 2),
            total_expenses=round(total_exp, 2),
            total_net_income=round(total_ni, 2),
            unassigned_revenue=round(unassigned_rev, 2),
            unassigned_expenses=round(unassigned_exp, 2),
        )

    def get_hierarchy(self, parent_code: Optional[str] = None) -> list[dict]:
        """Get cost center hierarchy as a tree structure.

        Args:
            parent_code: If None, returns full tree from roots.
                         If specified, returns subtree under that parent.

        Returns:
            List of tree nodes with 'cost_center' and 'children' keys.
        """
        all_centers = self.list()
        code_map = {c.code: c for c in all_centers}

        def build_tree(parent: Optional[str]) -> list[dict]:
            children = [c for c in all_centers if c.parent_code == parent]
            tree = []
            for cc in sorted(children, key=lambda c: c.code):
                node = {
                    "code": cc.code,
                    "name": cc.name,
                    "center_type": cc.center_type,
                    "active": cc.active,
                    "children": build_tree(cc.code),
                }
                tree.append(node)
            return tree

        return build_tree(parent_code)


# ── Formatting ───────────────────────────────────────────────

def format_cost_center_report(report: CostCenterReport) -> str:
    """Format a cost center report as text."""
    lines = []
    cc = report.cost_center
    if cc:
        lines.append(f"COST CENTER REPORT: {cc.name} ({cc.code})")
        lines.append(f"Type: {cc.center_type}")
    else:
        lines.append("COST CENTER REPORT")

    date_range = ""
    if report.from_date and report.to_date:
        date_range = f"{report.from_date.strftime('%Y-%m-%d')} to {report.to_date.strftime('%Y-%m-%d')}"
    elif report.to_date:
        date_range = f"As of {report.to_date.strftime('%Y-%m-%d')}"
    if date_range:
        lines.append(f"Period: {date_range}")
    lines.append(f"Entries: {report.entry_count}")
    lines.append("")

    if not report.lines:
        lines.append("(No activity for this cost center)")
        return "\n".join(lines)

    lines.append(f"{'Code':<12} {'Account':<30} {'Type':<10} {'Debit':>12} {'Credit':>12} {'Balance':>12}")
    lines.append("-" * 90)
    for l in report.lines:
        lines.append(
            f"{l.account_code:<12} {l.account_name:<30} {l.account_type.value:<10} "
            f"{l.debit:>12,.2f} {l.credit:>12,.2f} {l.balance:>12,.2f}"
        )
    lines.append("-" * 90)

    lines.append("")
    lines.append(f"{'Total Revenue':<42} {report.total_revenue:>12,.2f}")
    lines.append(f"{'Total Expenses':<42} {report.total_expenses:>12,.2f}")
    lines.append(f"{'Net Income':<42} {report.net_income:>12,.2f}")
    if report.total_assets:
        lines.append(f"{'Total Assets':<42} {report.total_assets:>12,.2f}")
    if report.total_liabilities:
        lines.append(f"{'Total Liabilities':<42} {report.total_liabilities:>12,.2f}")

    return "\n".join(lines)


def format_cost_center_summary(summary: CostCenterSummary) -> str:
    """Format a cost center summary as text."""
    lines = []
    lines.append("COST CENTER SUMMARY")
    lines.append("")
    lines.append(f"{'Code':<16} {'Name':<25} {'Type':<12} {'Revenue':>12} {'Expenses':>12} {'Net Income':>12} {'Entries':>8}")
    lines.append("-" * 100)

    for c in summary.centers:
        active_tag = "" if c["active"] else " (inactive)"
        lines.append(
            f"{c['code']:<16} {c['name'] + active_tag:<25} {c['center_type']:<12} "
            f"{c['revenue']:>12,.2f} {c['expenses']:>12,.2f} {c['net_income']:>12,.2f} {c['entry_count']:>8}"
        )

    lines.append("-" * 100)
    lines.append(f"{'TOTAL (assigned)':<53} {summary.total_revenue:>12,.2f} {summary.total_expenses:>12,.2f} {summary.total_net_income:>12,.2f}")
    lines.append(f"{'Unassigned':<53} {summary.unassigned_revenue:>12,.2f} {summary.unassigned_expenses:>12,.2f}")

    combined_rev = round(summary.total_revenue + summary.unassigned_revenue, 2)
    combined_exp = round(summary.total_expenses + summary.unassigned_expenses, 2)
    lines.append(f"{'GRAND TOTAL':<53} {combined_rev:>12,.2f} {combined_exp:>12,.2f}")

    return "\n".join(lines)
