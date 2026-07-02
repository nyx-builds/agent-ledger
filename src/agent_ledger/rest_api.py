"""REST API server for agent-ledger — FastAPI-based HTTP API for programmatic ledger access."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel as PydanticModel
from pydantic import Field

from .models import AccountType, JournalLine, Account
from .storage import Storage
from .ledger import Ledger
from .reports import (
    generate_trial_balance, generate_income_statement, generate_balance_sheet,
    format_trial_balance, format_income_statement, format_balance_sheet,
)
from .cashflow import generate_cash_flow_statement, format_cash_flow_statement
from .closing import close_period
from .hierarchy import AccountHierarchy
from .audit import AuditAction
from .exceptions import LedgerError
from .api_keys import APIKeyManager
from .alerts import AlertManager, AlertCondition, AlertSeverity


# ── Request/Response Models ────────────────────────────────────────

class InitLedgerRequest(PydanticModel):
    name: str = Field(default="Default Ledger")
    base_currency: str = Field(default="USD")


class CreateAccountRequest(PydanticModel):
    code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    account_type: str = Field(..., description="asset, liability, equity, revenue, expense")
    currency: str = Field(default="USD")
    description: str = Field(default="")
    parent_code: Optional[str] = None


class UpdateAccountRequest(PydanticModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    tags: Optional[list[str]] = None


class JournalLineRequest(PydanticModel):
    account_code: str
    debit: float = Field(default=0.0, ge=0)
    credit: float = Field(default=0.0, ge=0)


class PostEntryRequest(PydanticModel):
    description: str = Field(..., min_length=1)
    lines: list[JournalLineRequest] = Field(..., min_length=2)
    tags: list[str] = Field(default_factory=list)


class ReverseEntryRequest(PydanticModel):
    reason: Optional[str] = None


class ReconcileEntryRequest(PydanticModel):
    entry_ids: list[str] = Field(..., min_length=1)


class ClosePeriodRequest(PydanticModel):
    retained_earnings_code: str = Field(default="retained_earnings")
    description: Optional[str] = None


class AddExchangeRateRequest(PydanticModel):
    from_currency: str = Field(..., min_length=3, max_length=3)
    to_currency: str = Field(..., min_length=3, max_length=3)
    rate: float = Field(..., gt=0)
    source: str = Field(default="manual")


# ── App Factory ────────────────────────────────────────────────────

def create_app(ledger_path: str = "ledger.json") -> FastAPI:
    """Create the FastAPI application."""

    storage = Storage(Path(ledger_path))
    _ledger: Optional[Ledger] = None

    def get_ledger() -> Ledger:
        nonlocal _ledger
        if _ledger is None:
            if storage.exists():
                _ledger = Ledger(storage)
                _ledger.reload()
            else:
                raise HTTPException(status_code=503, detail="Ledger not initialized. POST /init first.")
        return _ledger

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(
        title="Agent Ledger",
        description="Double-entry accounting ledger REST API for autonomous agents",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health ───────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "1.0.0", "timestamp": datetime.now(timezone.utc).isoformat()}

    # ── Init ─────────────────────────────────────────────────────

    @app.post("/init", status_code=201)
    async def init_ledger(req: InitLedgerRequest):
        nonlocal _ledger
        if storage.exists():
            raise HTTPException(status_code=409, detail="Ledger already initialized")
        data = storage.init(name=req.name, base_currency=req.base_currency)
        _ledger = Ledger(storage)
        _ledger._data = data
        return {"status": "initialized", "name": data.name, "base_currency": data.base_currency}

    # ── Accounts ────────────────────────────────────────────────

    @app.post("/accounts", status_code=201)
    async def create_account(req: CreateAccountRequest):
        ledger = get_ledger()
        try:
            account = ledger.create_account(
                code=req.code,
                name=req.name,
                account_type=AccountType(req.account_type),
                currency=req.currency,
                description=req.description,
                parent_code=req.parent_code,
            )
            balance = ledger.get_account_balance(account.code)
            return {
                **_account_to_dict(account),
                "balance": balance.balance,
                "raw_balance": balance.raw_balance,
            }
        except LedgerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/accounts")
    async def list_accounts(
        account_type: Optional[str] = Query(None),
        active_only: bool = Query(False),
    ):
        ledger = get_ledger()
        at = AccountType(account_type) if account_type else None
        accounts = ledger.list_accounts(account_type=at, active_only=active_only)
        result = []
        for a in accounts:
            balance = ledger.get_account_balance(a.code)
            result.append({
                **_account_to_dict(a),
                "balance": balance.balance,
                "raw_balance": balance.raw_balance,
            })
        return result

    @app.get("/accounts/{code}")
    async def get_account(code: str):
        ledger = get_ledger()
        try:
            account = ledger.get_account(code)
            balance = ledger.get_account_balance(code)
            transactions = ledger.get_account_transactions(code)
            return {
                **_account_to_dict(account),
                "balance": balance.balance,
                "raw_balance": balance.raw_balance,
                "debit_total": balance.debit_total,
                "credit_total": balance.credit_total,
                "transaction_count": len(transactions),
            }
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.patch("/accounts/{code}")
    async def update_account(code: str, req: UpdateAccountRequest):
        ledger = get_ledger()
        try:
            account = ledger.update_account(
                code, name=req.name, description=req.description,
                active=req.active, tags=req.tags,
            )
            return _account_to_dict(account)
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.delete("/accounts/{code}")
    async def delete_account(code: str):
        ledger = get_ledger()
        try:
            ledger.delete_account(code)
            return {"deleted": True, "code": code}
        except LedgerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/accounts/{code}/transactions")
    async def get_account_transactions(code: str):
        ledger = get_ledger()
        try:
            return ledger.get_account_transactions(code)
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ── Journal Entries ─────────────────────────────────────────

    @app.post("/entries", status_code=201)
    async def post_entry(req: PostEntryRequest):
        ledger = get_ledger()
        try:
            lines = [
                JournalLine(
                    account_code=l.account_code,
                    debit=l.debit,
                    credit=l.credit,
                )
                for l in req.lines
            ]
            entry = ledger.post_entry(
                description=req.description,
                lines=lines,
                tags=req.tags,
            )
            return _entry_to_dict(entry)
        except LedgerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/entries")
    async def list_entries(
        account_code: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        reconciled: Optional[bool] = Query(None),
        start_date: Optional[str] = Query(None, description="ISO 8601 date"),
        end_date: Optional[str] = Query(None, description="ISO 8601 date"),
        limit: int = Query(50, ge=1, le=500),
    ):
        ledger = get_ledger()
        sd = _parse_date(start_date)
        ed = _parse_date(end_date)
        entries = ledger.list_entries(
            account_code=account_code,
            tag=tag,
            reconciled=reconciled,
            start_date=sd,
            end_date=ed,
        )
        return [_entry_to_dict(e) for e in entries[-limit:]]

    @app.get("/entries/{entry_id}")
    async def get_entry(entry_id: str):
        ledger = get_ledger()
        try:
            entry = ledger.get_entry(entry_id)
            return _entry_to_dict(entry)
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.delete("/entries/{entry_id}")
    async def delete_entry(entry_id: str):
        ledger = get_ledger()
        try:
            ledger.delete_entry(entry_id)
            return {"deleted": True, "entry_id": entry_id}
        except LedgerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/entries/{entry_id}/reconcile")
    async def reconcile_entry(entry_id: str):
        ledger = get_ledger()
        try:
            entry = ledger.reconcile_entry(entry_id)
            return {"status": "reconciled", "entry_id": entry.id}
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/entries/{entry_id}/unreconcile")
    async def unreconcile_entry(entry_id: str):
        ledger = get_ledger()
        try:
            entry = ledger.unreconcile_entry(entry_id)
            return {"status": "unreconciled", "entry_id": entry.id}
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/entries/{entry_id}/reverse")
    async def reverse_entry(entry_id: str, req: ReverseEntryRequest):
        ledger = get_ledger()
        try:
            reversal = ledger.reverse_entry(entry_id, reason=req.reason)
            return {
                "status": "reversed",
                "original_entry_id": entry_id,
                "reversal_entry_id": reversal.id,
                "description": reversal.description,
            }
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/entries/batch-reconcile")
    async def batch_reconcile(req: ReconcileEntryRequest):
        ledger = get_ledger()
        results = []
        for eid in req.entry_ids:
            try:
                entry = ledger.reconcile_entry(eid)
                results.append({"entry_id": eid, "status": "reconciled"})
            except LedgerError as e:
                results.append({"entry_id": eid, "status": "error", "error": str(e)})
        return {"results": results}

    # ── Reports ─────────────────────────────────────────────────

    @app.get("/reports/trial-balance")
    async def trial_balance(as_of: Optional[str] = Query(None)):
        ledger = get_ledger()
        as_of_dt = _parse_date(as_of)
        tb = generate_trial_balance(ledger, as_of=as_of_dt)
        return {
            "rows": [
                {
                    "account_code": r.account_code,
                    "account_name": r.account_name,
                    "account_type": r.account_type.value,
                    "debit": r.debit,
                    "credit": r.credit,
                }
                for r in tb.rows
            ],
            "total_debits": tb.total_debits,
            "total_credits": tb.total_credits,
            "is_balanced": tb.is_balanced,
            "as_of": tb.as_of.isoformat() if tb.as_of else None,
        }

    @app.get("/reports/income-statement")
    async def income_statement(
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
    ):
        ledger = get_ledger()
        fd = _parse_date(from_date)
        td = _parse_date(to_date)
        ist = generate_income_statement(ledger, from_date=fd, to_date=td)
        return {
            "revenue": [
                {"account_code": r.account_code, "account_name": r.account_name, "amount": r.amount}
                for r in ist.revenue_rows
            ],
            "expenses": [
                {"account_code": r.account_code, "account_name": r.account_name, "amount": r.amount}
                for r in ist.expense_rows
            ],
            "total_revenue": ist.total_revenue,
            "total_expenses": ist.total_expenses,
            "net_income": ist.net_income,
            "from_date": ist.from_date.isoformat() if ist.from_date else None,
            "to_date": ist.to_date.isoformat() if ist.to_date else None,
        }

    @app.get("/reports/balance-sheet")
    async def balance_sheet(as_of: Optional[str] = Query(None)):
        ledger = get_ledger()
        as_of_dt = _parse_date(as_of)
        bs = generate_balance_sheet(ledger, as_of=as_of_dt)
        return {
            "assets": [
                {"account_code": r.account_code, "account_name": r.account_name, "amount": r.amount}
                for r in bs.assets
            ],
            "liabilities": [
                {"account_code": r.account_code, "account_name": r.account_name, "amount": r.amount}
                for r in bs.liabilities
            ],
            "equity": [
                {"account_code": r.account_code, "account_name": r.account_name, "amount": r.amount}
                for r in bs.equity_rows
            ],
            "total_assets": bs.total_assets,
            "total_liabilities": bs.total_liabilities,
            "total_equity": bs.total_equity,
            "retained_earnings": bs.retained_earnings,
            "as_of": bs.as_of.isoformat() if bs.as_of else None,
        }

    @app.get("/reports/cash-flow")
    async def cash_flow(
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
    ):
        ledger = get_ledger()
        fd = _parse_date(from_date)
        td = _parse_date(to_date)
        cf = generate_cash_flow_statement(ledger, from_date=fd, to_date=td)
        return {
            "operating": {
                "items": [{"description": i.description, "amount": i.amount, "account_code": i.account_code} for i in cf.operating.items],
                "total": cf.operating.total,
            },
            "investing": {
                "items": [{"description": i.description, "amount": i.amount, "account_code": i.account_code} for i in cf.investing.items],
                "total": cf.investing.total,
            },
            "financing": {
                "items": [{"description": i.description, "amount": i.amount, "account_code": i.account_code} for i in cf.financing.items],
                "total": cf.financing.total,
            },
            "net_change_in_cash": cf.net_change_in_cash,
            "beginning_cash": cf.beginning_cash,
            "ending_cash": cf.ending_cash,
        }

    # ── Period Close ─────────────────────────────────────────────

    @app.post("/period/close")
    async def close_period_endpoint(req: ClosePeriodRequest):
        ledger = get_ledger()
        try:
            result = close_period(
                ledger,
                retained_earnings_code=req.retained_earnings_code,
                description=req.description,
            )
            return {
                "status": "closed",
                "closing_entry_id": result.closing_entry.id,
                "revenue_accounts_closed": result.revenue_accounts_closed,
                "expense_accounts_closed": result.expense_accounts_closed,
                "net_income": result.net_income,
                "retained_earnings_account": result.retained_earnings_account,
                "closed_at": result.closed_at.isoformat(),
            }
        except LedgerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/period/closes")
    async def list_closed_periods():
        ledger = get_ledger()
        return ledger.get_closed_periods()

    # ── Hierarchy ────────────────────────────────────────────────

    @app.get("/hierarchy")
    async def get_hierarchy(root_code: Optional[str] = Query(None)):
        ledger = get_ledger()
        h = AccountHierarchy(ledger)
        tree = h.get_tree(root_code=root_code)
        return _serialize_tree(tree)

    @app.get("/hierarchy/{code}/rollup")
    async def get_rollup_balance(code: str):
        ledger = get_ledger()
        try:
            h = AccountHierarchy(ledger)
            rollup = h.get_rollup_balance(code)
            return rollup.model_dump(mode="json")
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/hierarchy/validate")
    async def validate_hierarchy():
        ledger = get_ledger()
        h = AccountHierarchy(ledger)
        warnings = h.validate_hierarchy()
        return {"valid": len(warnings) == 0, "warnings": warnings}

    # ── Audit Log ───────────────────────────────────────────────

    @app.get("/audit")
    async def list_audit_log(
        action: Optional[str] = Query(None),
        actor: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=500),
    ):
        ledger = get_ledger()
        action_enum = AuditAction(action) if action else None
        entries = ledger.audit.list_entries(action=action_enum, actor=actor, limit=limit)
        return [e.model_dump(mode="json") for e in entries]

    # ── Exchange Rates ───────────────────────────────────────────

    @app.post("/exchange-rates", status_code=201)
    async def add_exchange_rate(req: AddExchangeRateRequest):
        ledger = get_ledger()
        er = ledger.add_exchange_rate(req.from_currency, req.to_currency, req.rate, req.source)
        return {"status": "added", "from": er.from_currency, "to": er.to_currency, "rate": er.rate}

    @app.get("/exchange-rates")
    async def list_exchange_rates():
        ledger = get_ledger()
        converter = ledger.get_currency_converter()
        return [r.model_dump(mode="json") for r in converter.list_rates()]

    # ── v0.5.0: Tax Summary ─────────────────────────────────────

    @app.get("/reports/tax-summary")
    async def tax_summary(
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        tax_rate: float = Query(0.0, ge=0, le=1),
    ):
        ledger = get_ledger()
        from .tax import generate_tax_summary
        fd = _parse_date(from_date)
        td = _parse_date(to_date)
        report = generate_tax_summary(ledger, from_date=fd, to_date=td, tax_rate=tax_rate)
        return {
            "period_start": report.period_start.isoformat() if report.period_start else None,
            "period_end": report.period_end.isoformat() if report.period_end else None,
            "items": [
                {
                    "account_code": i.account_code,
                    "account_name": i.account_name,
                    "account_type": i.account_type.value,
                    "amount": i.amount,
                    "tax_category": i.tax_category,
                    "tax_code": i.tax_code,
                    "deductible": i.deductible,
                }
                for i in report.items
            ],
            "total_revenue": report.total_revenue,
            "total_deductible_expenses": report.total_deductible_expenses,
            "total_nondeductible_expenses": report.total_nondeductible_expenses,
            "taxable_income": report.taxable_income,
            "estimated_tax": report.estimated_tax,
            "tax_rate_used": report.tax_rate_used,
        }

    # ── v0.5.0: General Ledger Report ───────────────────────────

    @app.get("/reports/general-ledger")
    async def general_ledger(
        account_code: Optional[str] = Query(None),
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
    ):
        ledger = get_ledger()
        from .general_ledger import generate_general_ledger
        fd = _parse_date(from_date)
        td = _parse_date(to_date)
        report = generate_general_ledger(
            ledger,
            account_code=account_code,
            from_date=fd,
            to_date=td,
            tag=tag,
        )
        return {
            "lines": [
                {
                    "entry_id": l.entry_id,
                    "timestamp": l.timestamp.isoformat(),
                    "account_code": l.account_code,
                    "account_name": l.account_name,
                    "account_type": l.account_type.value,
                    "description": l.description,
                    "debit": l.debit,
                    "credit": l.credit,
                    "running_balance": l.running_balance,
                }
                for l in report.lines
            ],
            "total_debits": report.total_debits,
            "total_credits": report.total_credits,
            "total_entries": report.total_entries,
        }

    # ── v0.5.0: Budgets ─────────────────────────────────────────

    @app.post("/budgets", status_code=201)
    async def create_budget(req: dict):
        ledger = get_ledger()
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        ps = _parse_date(req.get("period_start"))
        pe = _parse_date(req.get("period_end"))
        budget = bm.create_budget(
            name=req["name"],
            period_start=ps,
            period_end=pe,
            budget_lines=req.get("lines"),
        )
        return {
            "id": budget.id,
            "name": budget.name,
            "status": budget.status,
            "lines": len(budget.lines),
            "total_budgeted": budget.total_budgeted,
        }

    @app.get("/budgets")
    async def list_budgets(
        status: Optional[str] = Query(None),
    ):
        ledger = get_ledger()
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        budgets = bm.list_budgets(status=status)
        return [
            {
                "id": b.id,
                "name": b.name,
                "status": b.status,
                "lines": len(b.lines),
                "total_budgeted": b.total_budgeted,
                "total_actual": b.total_actual,
                "total_variance": b.total_variance,
            }
            for b in budgets
        ]

    @app.get("/budgets/{budget_id}")
    async def get_budget(budget_id: str):
        ledger = get_ledger()
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        budget = bm.get_budget(budget_id)
        return {
            "id": budget.id,
            "name": budget.name,
            "status": budget.status,
            "period_start": budget.period_start.isoformat() if budget.period_start else None,
            "period_end": budget.period_end.isoformat() if budget.period_end else None,
            "lines": [
                {
                    "account_code": l.account_code,
                    "budgeted_amount": l.budgeted_amount,
                    "actual_amount": l.actual_amount,
                    "variance": l.variance,
                    "variance_pct": l.variance_pct,
                }
                for l in budget.lines
            ],
            "total_budgeted": budget.total_budgeted,
            "total_actual": budget.total_actual,
            "total_variance": budget.total_variance,
        }

    @app.post("/budgets/{budget_id}/lines", status_code=201)
    async def add_budget_line(budget_id: str, req: dict):
        ledger = get_ledger()
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        line = bm.add_budget_line(
            budget_id=budget_id,
            account_code=req["account_code"],
            budgeted_amount=req["budgeted_amount"],
        )
        return {
            "account_code": line.account_code,
            "budgeted_amount": line.budgeted_amount,
            "status": "added",
        }

    @app.delete("/budgets/{budget_id}/lines/{account_code}")
    async def remove_budget_line(budget_id: str, account_code: str):
        ledger = get_ledger()
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        bm.remove_budget_line(budget_id, account_code)
        return {"status": "removed", "account_code": account_code}

    @app.post("/budgets/{budget_id}/activate")
    async def activate_budget(budget_id: str):
        ledger = get_ledger()
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        budget = bm.activate_budget(budget_id)
        return {"id": budget.id, "status": budget.status}

    @app.post("/budgets/{budget_id}/close")
    async def close_budget(budget_id: str):
        ledger = get_ledger()
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        budget = bm.close_budget(budget_id)
        return {"id": budget.id, "status": budget.status}

    @app.get("/budgets/{budget_id}/variance")
    async def budget_variance(budget_id: str):
        ledger = get_ledger()
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        report = bm.get_variance_report(budget_id)
        return {
            "budget_id": report.budget_id,
            "budget_name": report.budget_name,
            "lines": [
                {
                    "account_code": l.account_code,
                    "budgeted_amount": l.budgeted_amount,
                    "actual_amount": l.actual_amount,
                    "variance": l.variance,
                    "variance_pct": l.variance_pct,
                }
                for l in report.lines
            ],
            "favorable_lines": report.favorable_lines,
            "unfavorable_lines": report.unfavorable_lines,
            "on_budget_lines": report.on_budget_lines,
            "total_budgeted": report.total_budgeted,
            "total_actual": report.total_actual,
            "total_variance": report.total_variance,
        }

    @app.delete("/budgets/{budget_id}")
    async def delete_budget(budget_id: str):
        ledger = get_ledger()
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        bm.delete_budget(budget_id)
        return {"deleted": True, "budget_id": budget_id}

    # ── v0.5.0: Fiscal Years ────────────────────────────────────

    @app.post("/fiscal-years", status_code=201)
    async def create_fiscal_year(req: dict):
        ledger = get_ledger()
        from .fiscal import FiscalYearManager
        fm = FiscalYearManager(ledger)
        sd = _parse_date(req["start_date"])
        ed = _parse_date(req["end_date"])
        if sd is None or ed is None:
            raise HTTPException(status_code=400, detail="start_date and end_date are required")
        fy = fm.create_fiscal_year(
            name=req["name"],
            start_date=sd,
            end_date=ed,
            auto_periods=req.get("auto_periods", True),
            period_type=req.get("period_type", "month"),
        )
        return {
            "id": fy.id,
            "name": fy.name,
            "status": fy.status,
            "start_date": fy.start_date.isoformat(),
            "end_date": fy.end_date.isoformat(),
            "periods": [
                {
                    "name": p.name,
                    "start_date": p.start_date.isoformat(),
                    "end_date": p.end_date.isoformat(),
                    "status": p.status,
                    "period_type": p.period_type,
                }
                for p in fy.periods
            ],
        }

    @app.get("/fiscal-years")
    async def list_fiscal_years(
        status: Optional[str] = Query(None),
    ):
        ledger = get_ledger()
        from .fiscal import FiscalYearManager
        fm = FiscalYearManager(ledger)
        years = fm.list_fiscal_years(status=status)
        return [
            {
                "id": fy.id,
                "name": fy.name,
                "status": fy.status,
                "start_date": fy.start_date.isoformat(),
                "end_date": fy.end_date.isoformat(),
                "periods": len(fy.periods),
            }
            for fy in years
        ]

    @app.get("/fiscal-years/active")
    async def get_active_fiscal_year():
        ledger = get_ledger()
        from .fiscal import FiscalYearManager
        fm = FiscalYearManager(ledger)
        fy = fm.get_active_fiscal_year()
        if fy is None:
            return {"active_fiscal_year": None}
        return {
            "id": fy.id,
            "name": fy.name,
            "status": fy.status,
            "start_date": fy.start_date.isoformat(),
            "end_date": fy.end_date.isoformat(),
            "periods": [
                {
                    "name": p.name,
                    "start_date": p.start_date.isoformat(),
                    "end_date": p.end_date.isoformat(),
                    "status": p.status,
                    "period_type": p.period_type,
                }
                for p in fy.periods
            ],
        }

    @app.post("/fiscal-years/{fy_id}/close")
    async def close_fiscal_year(fy_id: str):
        ledger = get_ledger()
        from .fiscal import FiscalYearManager
        fm = FiscalYearManager(ledger)
        fy = fm.close_fiscal_year(fy_id)
        return {"id": fy.id, "name": fy.name, "status": fy.status}

    # ── v0.5.0: Bank Reconciliation REST Endpoints ──────────────

    @app.post("/reconciliation/statements", status_code=201)
    async def create_bank_statement(req: dict):
        ledger = get_ledger()
        from .reconciliation import BankReconciliation
        recon = BankReconciliation(ledger)
        stmt = recon.create_statement(
            account_code=req["account_code"],
            statement_date=_parse_date(req.get("statement_date")),
            opening_balance=req.get("opening_balance", 0.0),
            closing_balance=req.get("closing_balance", 0.0),
        )
        return {
            "id": stmt.id,
            "account_code": stmt.account_code,
            "statement_date": stmt.statement_date.isoformat() if stmt.statement_date else None,
            "opening_balance": stmt.opening_balance,
            "closing_balance": stmt.closing_balance,
            "status": stmt.status,
            "lines": len(stmt.lines),
        }

    @app.get("/reconciliation/statements")
    async def list_bank_statements(
        account_code: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
    ):
        ledger = get_ledger()
        from .reconciliation import BankReconciliation
        recon = BankReconciliation(ledger)
        statements = recon.list_statements(account_code=account_code, status=status)
        return [
            {
                "id": s.id,
                "account_code": s.account_code,
                "statement_date": s.statement_date.isoformat() if s.statement_date else None,
                "opening_balance": s.opening_balance,
                "closing_balance": s.closing_balance,
                "status": s.status,
                "lines": len(s.lines),
            }
            for s in statements
        ]

    @app.get("/reconciliation/statements/{statement_id}")
    async def get_bank_statement(statement_id: str):
        ledger = get_ledger()
        from .reconciliation import BankReconciliation
        recon = BankReconciliation(ledger)
        stmt = recon.get_statement(statement_id)
        return {
            "id": stmt.id,
            "account_code": stmt.account_code,
            "statement_date": stmt.statement_date.isoformat() if stmt.statement_date else None,
            "opening_balance": stmt.opening_balance,
            "closing_balance": stmt.closing_balance,
            "status": stmt.status,
            "lines": [
                {
                    "id": l.id,
                    "date": l.date.isoformat() if l.date else None,
                    "description": l.description,
                    "amount": l.amount,
                    "reference": l.reference,
                    "status": l.status,
                    "matched_entry_id": l.matched_entry_id,
                }
                for l in stmt.lines
            ],
        }

    @app.post("/reconciliation/statements/{statement_id}/match")
    async def match_statement_line(statement_id: str, req: dict):
        ledger = get_ledger()
        from .reconciliation import BankReconciliation
        recon = BankReconciliation(ledger)
        line = recon.match_entry(statement_id, req["line_id"], req["entry_id"])
        return {"matched": True, "line_id": line.id, "entry_id": req["entry_id"]}

    @app.post("/reconciliation/statements/{statement_id}/auto-match")
    async def auto_match_statement(statement_id: str, tolerance: float = Query(0.01)):
        ledger = get_ledger()
        from .reconciliation import BankReconciliation
        recon = BankReconciliation(ledger)
        result = recon.auto_match(statement_id, tolerance=tolerance)
        return result

    @app.post("/reconciliation/statements/{statement_id}/complete")
    async def complete_reconciliation(statement_id: str):
        ledger = get_ledger()
        from .reconciliation import BankReconciliation
        recon = BankReconciliation(ledger)
        result = recon.complete_reconciliation(statement_id)
        return {
            "statement_id": result.statement_id,
            "matched": result.matched,
            "difference": result.difference,
            "is_balanced": result.is_balanced,
        }

    @app.delete("/reconciliation/statements/{statement_id}")
    async def delete_bank_statement(statement_id: str):
        ledger = get_ledger()
        from .reconciliation import BankReconciliation
        recon = BankReconciliation(ledger)
        recon.delete_statement(statement_id)
        return {"deleted": True, "statement_id": statement_id}

    # ── v0.6.0: Search & Account Tags ────────────────────────────

    @app.get("/entries/search")
    async def search_entries(
        query: str = Query(..., description="Search string for entry descriptions"),
        account_code: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ):
        ledger = get_ledger()
        results = ledger.search_entries(
            query=query,
            account_code=account_code,
            tag=tag,
            limit=limit,
        )
        return [
            {
                "id": e.id,
                "description": e.description,
                "timestamp": e.timestamp.isoformat(),
                "tags": e.tags,
                "reconciled": e.reconciled,
                "total_debit": round(sum(l.debit for l in e.lines), 2),
                "total_credit": round(sum(l.credit for l in e.lines), 2),
            }
            for e in results
        ]

    @app.post("/accounts/{code}/tags/{tag}")
    async def add_account_tag(code: str, tag: str):
        ledger = get_ledger()
        account = ledger.get_account(code)
        if tag not in account.tags:
            account.tags.append(tag)
            ledger.save()
        return {"code": account.code, "name": account.name, "tags": account.tags}

    @app.delete("/accounts/{code}/tags/{tag}")
    async def remove_account_tag(code: str, tag: str):
        ledger = get_ledger()
        account = ledger.get_account(code)
        if tag in account.tags:
            account.tags.remove(tag)
            ledger.save()
        return {"code": account.code, "name": account.name, "tags": account.tags}

    @app.get("/accounts/by-tag/{tag}")
    async def list_accounts_by_tag(tag: str):
        ledger = get_ledger()
        accounts = ledger.list_accounts(tag=tag)
        return [_account_to_dict(a) for a in accounts]

    # ── v0.7.0: Recurring Entries ────────────────────────────────

    class RecurringLineRequest(PydanticModel):
        account_code: str
        debit: float = Field(default=0.0, ge=0)
        credit: float = Field(default=0.0, ge=0)

    class CreateRecurringRequest(PydanticModel):
        name: str = Field(..., min_length=1)
        description: str = Field(default="")
        lines: list[RecurringLineRequest] = Field(..., min_length=2)
        schedule_type: str = Field(default="monthly")
        interval: int = Field(default=1, ge=1)
        day_of_month: int = Field(default=1, ge=1, le=31)
        day_of_week: int = Field(default=0, ge=0, le=6)
        month_of_year: int = Field(default=1, ge=1, le=12)
        start_date: Optional[str] = None
        end_date: Optional[str] = None
        max_occurrences: Optional[int] = None
        tags: list[str] = Field(default_factory=list)

    @app.post("/recurring", status_code=201)
    async def create_recurring(req: CreateRecurringRequest):
        ledger = get_ledger()
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        try:
            template = rm.create(
                name=req.name,
                description=req.description,
                lines=[l.model_dump() for l in req.lines],
                schedule_type=req.schedule_type,
                interval=req.interval,
                day_of_month=req.day_of_month,
                day_of_week=req.day_of_week,
                month_of_year=req.month_of_year,
                start_date=_parse_date(req.start_date),
                end_date=_parse_date(req.end_date),
                max_occurrences=req.max_occurrences,
                tags=req.tags or None,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "id": template.id,
            "name": template.name,
            "schedule_type": template.schedule_type.value,
            "active": template.active,
            "next_run": template.next_run.isoformat() if template.next_run else None,
        }

    @app.get("/recurring")
    async def list_recurring(active_only: bool = Query(False)):
        ledger = get_ledger()
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        templates = rm.list_templates(active_only=active_only)
        return [
            {
                "id": t.id,
                "name": t.name,
                "schedule_type": t.schedule_type.value,
                "active": t.active,
                "occurrences_created": t.occurrences_created,
                "next_run": t.next_run.isoformat() if t.next_run else None,
            }
            for t in templates
        ]

    @app.get("/recurring/{template_id}")
    async def get_recurring(template_id: str):
        ledger = get_ledger()
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        try:
            t = rm.get(template_id)
        except Exception:
            raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
        return {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "schedule_type": t.schedule_type.value,
            "active": t.active,
            "lines": [
                {"account_code": l.account_code, "debit": l.debit, "credit": l.credit}
                for l in t.lines
            ],
            "next_run": t.next_run.isoformat() if t.next_run else None,
        }

    @app.post("/recurring/{template_id}/pause")
    async def pause_recurring(template_id: str):
        ledger = get_ledger()
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        try:
            t = rm.pause(template_id)
        except Exception:
            raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
        return {"id": t.id, "active": t.active}

    @app.post("/recurring/{template_id}/resume")
    async def resume_recurring(template_id: str):
        ledger = get_ledger()
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        try:
            t = rm.resume(template_id)
        except Exception:
            raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
        return {"id": t.id, "active": t.active}

    @app.delete("/recurring/{template_id}")
    async def delete_recurring(template_id: str):
        ledger = get_ledger()
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        try:
            rm.delete(template_id)
        except Exception:
            raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
        return {"deleted": True}

    @app.post("/recurring/process")
    async def process_recurring():
        ledger = get_ledger()
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        results = rm.process_all()
        return {
            "processed": len(results),
            "generated": sum(1 for r in results if r["status"] == "generated"),
            "results": results,
        }

    # ── v0.7.0: Financial Ratios ─────────────────────────────────

    @app.get("/reports/ratios")
    async def get_ratios(
        as_of: Optional[str] = Query(None, description="ISO 8601 date"),
        cash_tags: Optional[str] = Query(None, description="Comma-separated tags for cash accounts"),
        inventory_tags: Optional[str] = Query(None, description="Comma-separated tags for inventory"),
        current_tags: Optional[str] = Query(None, description="Comma-separated tags for current accounts"),
    ):
        ledger = get_ledger()
        from .ratios import compute_ratios
        ratios = compute_ratios(
            ledger,
            as_of=_parse_date(as_of),
            cash_tags=set(cash_tags.split(",")) if cash_tags else None,
            inventory_tags=set(inventory_tags.split(",")) if inventory_tags else None,
            current_tags=set(current_tags.split(",")) if current_tags else None,
        )
        return {
            "total_assets": ratios.total_assets,
            "total_liabilities": ratios.total_liabilities,
            "total_equity": ratios.total_equity,
            "net_income": ratios.net_income,
            "working_capital": ratios.working_capital,
            "current_ratio": ratios.current_ratio,
            "quick_ratio": ratios.quick_ratio,
            "cash_ratio": ratios.cash_ratio,
            "debt_to_equity": ratios.debt_to_equity,
            "profit_margin": ratios.profit_margin,
            "return_on_assets": ratios.return_on_assets,
            "return_on_equity": ratios.return_on_equity,
            "asset_turnover": ratios.asset_turnover,
            "warnings": ratios.warnings,
        }

    @app.get("/reports/health")
    async def get_health(as_of: Optional[str] = Query(None)):
        ledger = get_ledger()
        from .ratios import compute_ratios, get_financial_health
        ratios = compute_ratios(ledger, as_of=_parse_date(as_of))
        health = get_financial_health(ratios)
        return {
            "health": health,
            "net_income": ratios.net_income,
            "working_capital": ratios.working_capital,
            "warnings": ratios.warnings,
        }

    # ── v1.0.0: Balance Alerts ───────────────────────────────────

    @app.post("/alerts/rules", status_code=201)
    async def create_alert_rule(req: dict):
        ledger = get_ledger()
        am = AlertManager(ledger)
        try:
            rule = am.create_rule(
                name=req["name"],
                account_code=req["account_code"],
                condition=AlertCondition(req.get("condition", "above")),
                threshold=req["threshold"],
                severity=AlertSeverity(req.get("severity", "warning")),
                description=req.get("description", ""),
                cooldown_minutes=req.get("cooldown_minutes", 60),
            )
            return {
                "id": rule.id,
                "name": rule.name,
                "account_code": rule.account_code,
                "condition": rule.condition.value,
                "threshold": rule.threshold,
                "severity": rule.severity.value,
                "enabled": rule.enabled,
            }
        except (LedgerError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/alerts/rules")
    async def list_alert_rules(
        account_code: Optional[str] = Query(None),
        enabled_only: bool = Query(False),
    ):
        ledger = get_ledger()
        am = AlertManager(ledger)
        rules = am.list_rules(account_code=account_code, enabled_only=enabled_only)
        return [
            {
                "id": r.id,
                "name": r.name,
                "account_code": r.account_code,
                "condition": r.condition.value,
                "threshold": r.threshold,
                "severity": r.severity.value,
                "enabled": r.enabled,
                "cooldown_minutes": r.cooldown_minutes,
                "last_triggered": r.last_triggered.isoformat() if r.last_triggered else None,
            }
            for r in rules
        ]

    @app.delete("/alerts/rules/{rule_id}")
    async def delete_alert_rule(rule_id: str):
        ledger = get_ledger()
        am = AlertManager(ledger)
        try:
            am.delete_rule(rule_id)
            return {"deleted": True, "rule_id": rule_id}
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/alerts/check")
    async def check_alerts():
        ledger = get_ledger()
        am = AlertManager(ledger)
        triggers = am.check_rules()
        return {
            "checked": True,
            "triggered": len(triggers),
            "triggers": [
                {
                    "id": t.id,
                    "rule_id": t.rule_id,
                    "rule_name": t.rule_name,
                    "account_code": t.account_code,
                    "condition": t.condition,
                    "threshold": t.threshold,
                    "actual_value": t.actual_value,
                    "severity": t.severity,
                    "message": t.message,
                    "triggered_at": t.triggered_at.isoformat(),
                }
                for t in triggers
            ],
        }

    @app.get("/alerts/triggers")
    async def list_alert_triggers(
        rule_id: Optional[str] = Query(None),
        acknowledged: Optional[bool] = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ):
        ledger = get_ledger()
        am = AlertManager(ledger)
        triggers = am.list_triggers(rule_id=rule_id, acknowledged=acknowledged, limit=limit)
        return [
            {
                "id": t.id,
                "rule_id": t.rule_id,
                "rule_name": t.rule_name,
                "account_code": t.account_code,
                "condition": t.condition,
                "threshold": t.threshold,
                "actual_value": t.actual_value,
                "severity": t.severity,
                "message": t.message,
                "triggered_at": t.triggered_at.isoformat(),
                "acknowledged": t.acknowledged,
            }
            for t in triggers
        ]

    @app.post("/alerts/triggers/{trigger_id}/acknowledge")
    async def acknowledge_alert(trigger_id: str):
        ledger = get_ledger()
        am = AlertManager(ledger)
        try:
            t = am.acknowledge_trigger(trigger_id)
            return {"acknowledged": True, "trigger_id": t.id}
        except LedgerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/alerts/acknowledge-all")
    async def acknowledge_all_alerts(rule_id: Optional[str] = Query(None)):
        ledger = get_ledger()
        am = AlertManager(ledger)
        count = am.acknowledge_all(rule_id=rule_id)
        return {"acknowledged_count": count}

    # ── v1.0.0: API Key Management ───────────────────────────────

    @app.post("/api-keys", status_code=201)
    async def create_api_key(req: dict):
        ledger = get_ledger()
        km = APIKeyManager(ledger)
        try:
            key, raw_key = km.create_key(
                name=req["name"],
                scopes=req.get("scopes", ["read"]),
                description=req.get("description", ""),
                rate_limit_per_hour=req.get("rate_limit_per_hour"),
            )
            return {
                "id": key.id,
                "name": key.name,
                "key_prefix": key.key_prefix,
                "key": raw_key,  # Only returned at creation
                "scopes": key.scopes,
                "message": "Store this key securely — it won't be shown again.",
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api-keys")
    async def list_api_keys(active_only: bool = Query(False)):
        ledger = get_ledger()
        km = APIKeyManager(ledger)
        keys = km.list_keys(active_only=active_only)
        return [
            {
                "id": k.id,
                "name": k.name,
                "key_prefix": k.key_prefix,
                "scopes": k.scopes,
                "active": k.active,
                "created_at": k.created_at.isoformat(),
                "last_used": k.last_used.isoformat() if k.last_used else None,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "request_count": k.request_count,
                "rate_limit_per_hour": k.rate_limit_per_hour,
            }
            for k in keys
        ]

    @app.delete("/api-keys/{key_id}")
    async def revoke_api_key(key_id: str):
        ledger = get_ledger()
        km = APIKeyManager(ledger)
        try:
            km.revoke_key(key_id)
            return {"revoked": True, "key_id": key_id}
        except KeyError:
            raise HTTPException(status_code=404, detail="API key not found")

    @app.post("/api-keys/{key_id}/scopes")
    async def update_key_scopes(key_id: str, req: dict):
        ledger = get_ledger()
        km = APIKeyManager(ledger)
        try:
            key = km.update_key(key_id, scopes=req.get("scopes"))
            return {"id": key.id, "scopes": key.scopes}
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ── v1.0.0: Dashboard ────────────────────────────────────────

    @app.get("/dashboard")
    async def get_dashboard(format: str = Query("json", description="json or html")):
        ledger = get_ledger()
        if format == "html":
            from .dashboard import generate_dashboard_html
            from fastapi import Response
            html_content = generate_dashboard_html(ledger)
            return Response(content=html_content, media_type="text/html")
        else:
            from .reports import generate_trial_balance, generate_income_statement, generate_balance_sheet
            from .ratios import compute_ratios, get_financial_health
            tb = generate_trial_balance(ledger)
            ist = generate_income_statement(ledger)
            bs = generate_balance_sheet(ledger)
            ratios = compute_ratios(ledger)
            health = get_financial_health(ratios)
            return {
                "balance_sheet": {
                    "total_assets": bs.total_assets,
                    "total_liabilities": bs.total_liabilities,
                    "total_equity": bs.total_equity,
                    "retained_earnings": bs.retained_earnings,
                },
                "income_statement": {
                    "total_revenue": ist.total_revenue,
                    "total_expenses": ist.total_expenses,
                    "net_income": ist.net_income,
                },
                "trial_balance": {
                    "total_debits": tb.total_debits,
                    "total_credits": tb.total_credits,
                    "is_balanced": tb.is_balanced,
                },
                "ratios": {
                    "current_ratio": ratios.current_ratio,
                    "quick_ratio": ratios.quick_ratio,
                    "debt_to_equity": ratios.debt_to_equity,
                    "profit_margin": ratios.profit_margin,
                    "return_on_assets": ratios.return_on_assets,
                },
                "health": health,
                "summary": {
                    "total_accounts": len(ledger.list_accounts()),
                    "total_entries": len(ledger.data.entries),
                    "reconciled_entries": len([e for e in ledger.data.entries if e.reconciled]),
                },
            }

    return app

def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 date string to timezone-aware datetime."""
    if date_str is None:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        # Ensure timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str}. Use ISO 8601.")


def _account_to_dict(account: Account) -> dict:
    """Convert an account to a JSON-safe dict."""
    import json
    return json.loads(account.model_dump_json())


def _entry_to_dict(entry) -> dict:
    """Convert a journal entry to a JSON-safe dict."""
    import json
    return json.loads(entry.model_dump_json())


def _serialize_tree(tree: list[dict]) -> list[dict]:
    """Serialize account tree to JSON-safe dicts."""
    result = []
    for node in tree:
        result.append(_serialize_tree_node(node))
    return result


def _serialize_tree_node(node: dict) -> dict:
    """Serialize a single tree node."""
    import json
    account = node["account"]
    balance = node["balance"]
    rollup = node["rollup_balance"]
    return {
        "account": _account_to_dict(account),
        "balance": json.loads(balance.model_dump_json()),
        "rollup_balance": json.loads(rollup.model_dump_json()),
        "depth": node["depth"],
        "children": [_serialize_tree_node(c) for c in node["children"]],
    }
