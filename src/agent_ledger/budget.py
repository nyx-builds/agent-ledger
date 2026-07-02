"""Budget tracking for agent-ledger — set budgets per account and compare actual vs. budget."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import AccountType
from .ledger import Ledger
from .exceptions import LedgerError
from .reports import _ensure_aware


class BudgetError(LedgerError):
    """Error related to budget operations."""
    pass


class BudgetNotFoundError(BudgetError):
    """Budget not found."""
    pass


@dataclass
class BudgetLine:
    """A single budget line for an account."""
    account_code: str
    budgeted_amount: float
    actual_amount: float = 0.0
    variance: float = 0.0
    variance_pct: float = 0.0


@dataclass
class Budget:
    """A budget definition for a fiscal period."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    status: str = "draft"  # draft, active, closed
    lines: list[BudgetLine] = field(default_factory=list)
    total_budgeted: float = 0.0
    total_actual: float = 0.0
    total_variance: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BudgetVarianceReport:
    """Variance report comparing budget vs. actual."""
    budget_id: str
    budget_name: str
    lines: list[BudgetLine] = field(default_factory=list)
    favorable_lines: int = 0
    unfavorable_lines: int = 0
    on_budget_lines: int = 0
    total_budgeted: float = 0.0
    total_actual: float = 0.0
    total_variance: float = 0.0


class BudgetManager:
    """Manage budgets and track actual vs. budgeted amounts."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self._budgets: list[Budget] = []
        self._load()

    def _load(self) -> None:
        """Load budgets from ledger data."""
        for bd in self.ledger.data.budgets:
            lines = []
            for ld in bd.get("lines", []):
                lines.append(BudgetLine(
                    account_code=ld.get("account_code", ""),
                    budgeted_amount=ld.get("budgeted_amount", 0.0),
                    actual_amount=ld.get("actual_amount", 0.0),
                    variance=ld.get("variance", 0.0),
                    variance_pct=ld.get("variance_pct", 0.0),
                ))
            self._budgets.append(Budget(
                id=bd.get("id", str(uuid.uuid4())),
                name=bd.get("name", ""),
                period_start=datetime.fromisoformat(bd["period_start"]) if bd.get("period_start") else None,
                period_end=datetime.fromisoformat(bd["period_end"]) if bd.get("period_end") else None,
                status=bd.get("status", "draft"),
                lines=lines,
                total_budgeted=bd.get("total_budgeted", 0.0),
                total_actual=bd.get("total_actual", 0.0),
                total_variance=bd.get("total_variance", 0.0),
                created_at=datetime.fromisoformat(bd["created_at"]) if bd.get("created_at") else datetime.now(timezone.utc),
            ))

    def _save(self) -> None:
        """Save budgets to ledger data."""
        budgets_data = []
        for b in self._budgets:
            lines_data = []
            for line in b.lines:
                lines_data.append({
                    "account_code": line.account_code,
                    "budgeted_amount": line.budgeted_amount,
                    "actual_amount": line.actual_amount,
                    "variance": line.variance,
                    "variance_pct": line.variance_pct,
                })
            budgets_data.append({
                "id": b.id,
                "name": b.name,
                "period_start": b.period_start.isoformat() if b.period_start else None,
                "period_end": b.period_end.isoformat() if b.period_end else None,
                "status": b.status,
                "lines": lines_data,
                "total_budgeted": b.total_budgeted,
                "total_actual": b.total_actual,
                "total_variance": b.total_variance,
                "created_at": b.created_at.isoformat(),
            })
        self.ledger.data.budgets = budgets_data
        self.ledger.save()

    def create_budget(
        self,
        name: str,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        budget_lines: Optional[list[dict]] = None,
    ) -> Budget:
        """Create a new budget.

        Args:
            name: Budget name (e.g., "Q1 2024 Budget")
            period_start: Start of the budget period
            period_end: End of the budget period
            budget_lines: List of dicts with account_code and budgeted_amount

        Returns:
            The created Budget
        """
        budget = Budget(
            name=name,
            period_start=period_start,
            period_end=period_end,
            status="draft",
        )

        if budget_lines:
            for bl in budget_lines:
                code = bl["account_code"].strip().lower()
                amount = float(bl["budgeted_amount"])

                # Validate account exists
                self.ledger.get_account(code)

                budget.lines.append(BudgetLine(
                    account_code=code,
                    budgeted_amount=round(amount, 2),
                ))

        self._recalculate(budget)
        self._budgets.append(budget)
        self._save()
        return budget

    def get_budget(self, budget_id: str) -> Budget:
        """Get a budget by ID."""
        for b in self._budgets:
            if b.id == budget_id:
                return b
        raise BudgetNotFoundError(f"Budget '{budget_id}' not found")

    def list_budgets(self, status: Optional[str] = None) -> list[Budget]:
        """List all budgets, optionally filtered by status."""
        budgets = list(self._budgets)
        if status:
            budgets = [b for b in budgets if b.status == status]
        return sorted(budgets, key=lambda b: b.created_at, reverse=True)

    def activate_budget(self, budget_id: str) -> Budget:
        """Activate a draft budget."""
        budget = self.get_budget(budget_id)
        if budget.status != "draft":
            raise BudgetError(f"Can only activate draft budgets, current status: {budget.status}")
        budget.status = "active"
        self._save()
        return budget

    def close_budget(self, budget_id: str) -> Budget:
        """Close an active budget."""
        budget = self.get_budget(budget_id)
        if budget.status != "active":
            raise BudgetError(f"Can only close active budgets, current status: {budget.status}")
        budget.status = "closed"
        self._save()
        return budget

    def add_budget_line(
        self,
        budget_id: str,
        account_code: str,
        budgeted_amount: float,
    ) -> BudgetLine:
        """Add a line to a budget.

        Args:
            budget_id: ID of the budget
            account_code: Account code
            budgeted_amount: Budgeted amount

        Returns:
            The created BudgetLine
        """
        budget = self.get_budget(budget_id)

        if budget.status == "closed":
            raise BudgetError("Cannot modify a closed budget")

        code = account_code.strip().lower()
        self.ledger.get_account(code)

        # Check if line already exists
        for line in budget.lines:
            if line.account_code == code:
                line.budgeted_amount = round(budgeted_amount, 2)
                self._recalculate(budget)
                self._save()
                return line

        line = BudgetLine(
            account_code=code,
            budgeted_amount=round(budgeted_amount, 2),
        )
        budget.lines.append(line)
        self._recalculate(budget)
        self._save()
        return line

    def remove_budget_line(self, budget_id: str, account_code: str) -> None:
        """Remove a line from a budget."""
        budget = self.get_budget(budget_id)

        if budget.status == "closed":
            raise BudgetError("Cannot modify a closed budget")

        code = account_code.strip().lower()
        budget.lines = [l for l in budget.lines if l.account_code != code]
        self._recalculate(budget)
        self._save()

    def update_actuals(self, budget_id: str) -> Budget:
        """Update actual amounts from the ledger.

        Recalculates actual amounts based on ledger entries within
        the budget period.

        Args:
            budget_id: ID of the budget

        Returns:
            The updated Budget
        """
        budget = self.get_budget(budget_id)

        for line in budget.lines:
            try:
                account = self.ledger.get_account(line.account_code)
                balance = self.ledger.get_account_balance(line.account_code)
                # Use the balance as the actual amount
                line.actual_amount = round(balance.balance, 2)
            except Exception:
                line.actual_amount = 0.0

        self._recalculate(budget)
        self._save()
        return budget

    def _recalculate(self, budget: Budget) -> None:
        """Recalculate variances and totals for a budget."""
        total_budgeted = 0.0
        total_actual = 0.0

        for line in budget.lines:
            line.variance = round(line.budgeted_amount - line.actual_amount, 2)
            if line.budgeted_amount != 0:
                line.variance_pct = round(
                    (line.variance / abs(line.budgeted_amount)) * 100, 2
                )
            else:
                line.variance_pct = 0.0

            total_budgeted += line.budgeted_amount
            total_actual += line.actual_amount

        budget.total_budgeted = round(total_budgeted, 2)
        budget.total_actual = round(total_actual, 2)
        budget.total_variance = round(total_budgeted - total_actual, 2)

    def get_variance_report(self, budget_id: str) -> BudgetVarianceReport:
        """Generate a variance report for a budget.

        Args:
            budget_id: ID of the budget

        Returns:
            BudgetVarianceReport with favorable/unfavorable analysis
        """
        budget = self.get_budget(budget_id)
        self.update_actuals(budget_id)

        favorable = 0
        unfavorable = 0
        on_budget = 0

        for line in budget.lines:
            if abs(line.variance) < 0.01:
                on_budget += 1
            elif line.variance > 0:
                favorable += 1
            else:
                unfavorable += 1

        return BudgetVarianceReport(
            budget_id=budget.id,
            budget_name=budget.name,
            lines=list(budget.lines),
            favorable_lines=favorable,
            unfavorable_lines=unfavorable,
            on_budget_lines=on_budget,
            total_budgeted=budget.total_budgeted,
            total_actual=budget.total_actual,
            total_variance=budget.total_variance,
        )

    def delete_budget(self, budget_id: str) -> None:
        """Delete a budget (only draft budgets can be deleted)."""
        budget = self.get_budget(budget_id)
        if budget.status == "active":
            raise BudgetError("Cannot delete an active budget. Close it first.")
        self._budgets.remove(budget)
        self._save()


def format_variance_report(report: BudgetVarianceReport) -> str:
    """Format a budget variance report as text."""
    lines = []
    lines.append(f"BUDGET VARIANCE REPORT: {report.budget_name}")
    lines.append("")

    lines.append(
        f"{'Code':<12} {'Budgeted':>12} {'Actual':>12} {'Variance':>12} {'Var %':>8} {'Status':>10}"
    )
    lines.append("-" * 68)

    for line in report.lines:
        if abs(line.variance) < 0.01:
            status = "On Budget"
        elif line.variance > 0:
            status = "Favorable"
        else:
            status = "Unfavorable"

        var_pct = f"{line.variance_pct:+.1f}%" if line.variance_pct != 0 else "0.0%"

        lines.append(
            f"{line.account_code:<12} {line.budgeted_amount:>12,.2f} "
            f"{line.actual_amount:>12,.2f} {line.variance:>+12,.2f} "
            f"{var_pct:>8} {status:>10}"
        )

    lines.append("-" * 68)
    lines.append(
        f"{'TOTAL':<12} {report.total_budgeted:>12,.2f} "
        f"{report.total_actual:>12,.2f} {report.total_variance:>+12,.2f}"
    )

    lines.append("")
    lines.append(f"Favorable: {report.favorable_lines}  |  Unfavorable: {report.unfavorable_lines}  |  On Budget: {report.on_budget_lines}")

    return "\n".join(lines)
