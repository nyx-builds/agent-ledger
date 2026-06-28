"""MCP server for agent-ledger — Model Context Protocol integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .models import AccountType, JournalLine
from .storage import Storage
from .ledger import Ledger
from .reports import (
    generate_trial_balance, generate_income_statement, generate_balance_sheet,
    format_trial_balance, format_income_statement, format_balance_sheet,
)
from .closing import close_period
from .hierarchy import AccountHierarchy
from .audit import AuditAction
from .export import (
    export_accounts_csv, export_entries_csv, export_trial_balance_csv,
    export_income_statement_csv, export_balance_sheet_csv,
    export_hierarchy_csv,
)
from .exceptions import LedgerError


def _ledger_to_dict(ledger: Ledger) -> dict:
    """Convert ledger data to a serializable dict."""
    data = ledger.data
    return json.loads(data.model_dump_json())


def _entry_to_dict(entry) -> dict:
    """Convert a journal entry to dict."""
    return json.loads(entry.model_dump_json())


def _account_to_dict(account) -> dict:
    """Convert an account to dict."""
    return json.loads(account.model_dump_json())


# ── Tool Definitions ────────────────────────────────────────────

TOOLS = [
    {
        "name": "init_ledger",
        "description": "Initialize a new ledger",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Ledger name", "default": "Default Ledger"},
                "base_currency": {"type": "string", "description": "Base currency code", "default": "USD"},
            },
        },
    },
    {
        "name": "create_account",
        "description": "Create a new account in the chart of accounts",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Account code (e.g., 'cash', '1000')"},
                "name": {"type": "string", "description": "Account name"},
                "account_type": {
                    "type": "string",
                    "enum": ["asset", "liability", "equity", "revenue", "expense"],
                    "description": "Account type",
                },
                "currency": {"type": "string", "description": "Currency code", "default": "USD"},
                "description": {"type": "string", "description": "Account description", "default": ""},
                "parent_code": {"type": "string", "description": "Parent account code for hierarchy", "default": None},
            },
            "required": ["code", "name", "account_type"],
        },
    },
    {
        "name": "list_accounts",
        "description": "List all accounts with their balances",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_type": {
                    "type": "string",
                    "enum": ["asset", "liability", "equity", "revenue", "expense"],
                    "description": "Filter by account type",
                },
            },
        },
    },
    {
        "name": "get_account",
        "description": "Get account details and balance",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Account code"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "get_account_transactions",
        "description": "Get all transactions for an account with running balance",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Account code"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "post_entry",
        "description": "Post a journal entry (double-entry: debits must equal credits). "
                       "Lines format: list of {account_code, debit, credit} objects.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Entry description"},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "account_code": {"type": "string"},
                            "debit": {"type": "number", "default": 0},
                            "credit": {"type": "number", "default": 0},
                        },
                        "required": ["account_code"],
                    },
                    "description": "Journal lines (at least 2, must balance)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization",
                },
            },
            "required": ["description", "lines"],
        },
    },
    {
        "name": "list_entries",
        "description": "List journal entries with optional filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_code": {"type": "string", "description": "Filter by account"},
                "tag": {"type": "string", "description": "Filter by tag"},
                "limit": {"type": "integer", "description": "Max entries", "default": 20},
            },
        },
    },
    {
        "name": "get_entry",
        "description": "Get details of a specific journal entry",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "Entry ID"},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "delete_entry",
        "description": "Delete a journal entry (cannot delete reconciled entries)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "Entry ID"},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "reconcile_entry",
        "description": "Mark a journal entry as reconciled",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "Entry ID"},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "trial_balance",
        "description": "Generate a trial balance report",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "income_statement",
        "description": "Generate an income statement (profit & loss)",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "balance_sheet",
        "description": "Generate a balance sheet",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "add_exchange_rate",
        "description": "Add an exchange rate between currencies",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_currency": {"type": "string", "description": "Source currency code"},
                "to_currency": {"type": "string", "description": "Target currency code"},
                "rate": {"type": "number", "description": "Exchange rate"},
                "source": {"type": "string", "description": "Rate source", "default": "manual"},
            },
            "required": ["from_currency", "to_currency", "rate"],
        },
    },
    {
        "name": "list_exchange_rates",
        "description": "List all exchange rates",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    # ── v0.2.0 New Tools ────────────────────────────────────────
    {
        "name": "close_period",
        "description": "Close the current accounting period — zeros out revenue and expense accounts into retained earnings",
        "inputSchema": {
            "type": "object",
            "properties": {
                "retained_earnings_code": {
                    "type": "string",
                    "description": "Account code for retained earnings (created as equity if not exists)",
                    "default": "retained_earnings",
                },
                "description": {
                    "type": "string",
                    "description": "Custom description for the closing entry",
                    "default": None,
                },
            },
        },
    },
    {
        "name": "get_account_hierarchy",
        "description": "Get the account hierarchy tree with rollup balances",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root_code": {
                    "type": "string",
                    "description": "Optional root account code to start the tree from",
                    "default": None,
                },
            },
        },
    },
    {
        "name": "get_rollup_balance",
        "description": "Get the rolled-up balance for an account including all descendant accounts",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Account code"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "validate_hierarchy",
        "description": "Validate the account hierarchy for issues (missing parents, type mismatches, circular references)",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_audit_log",
        "description": "List audit log entries tracking all changes to the ledger",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [a.value for a in AuditAction],
                    "description": "Filter by action type",
                    "default": None,
                },
                "actor": {"type": "string", "description": "Filter by actor", "default": None},
                "limit": {"type": "integer", "description": "Max entries", "default": 50},
            },
        },
    },
    {
        "name": "export_csv",
        "description": "Export ledger data as CSV (accounts, entries, trial balance, income statement, balance sheet, or hierarchy)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["accounts", "entries", "trial_balance", "income_statement", "balance_sheet", "hierarchy"],
                    "description": "Type of data to export",
                },
                "account_code": {
                    "type": "string",
                    "description": "Filter by account (for entries export only)",
                    "default": None,
                },
                "tag": {
                    "type": "string",
                    "description": "Filter by tag (for entries export only)",
                    "default": None,
                },
            },
            "required": ["type"],
        },
    },
    {
        "name": "list_closed_periods",
        "description": "List all closed period records",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ── Tool Handler ────────────────────────────────────────────────

def handle_tool_call(ledger: Ledger, name: str, arguments: dict) -> list[dict]:
    """Handle a tool call and return content blocks."""
    try:
        result = _dispatch(ledger, name, arguments)
        return [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]
    except LedgerError as e:
        return [{"type": "text", "text": json.dumps({"error": str(e)})}]


def _dispatch(ledger: Ledger, name: str, args: dict) -> Any:
    """Dispatch a tool call to the appropriate ledger method."""
    if name == "init_ledger":
        data = ledger._storage.init(
            name=args.get("name", "Default Ledger"),
            base_currency=args.get("base_currency", "USD"),
        )
        return {"status": "initialized", "name": data.name, "base_currency": data.base_currency}

    elif name == "create_account":
        account = ledger.create_account(
            code=args["code"],
            name=args["name"],
            account_type=AccountType(args["account_type"]),
            currency=args.get("currency", "USD"),
            description=args.get("description", ""),
            parent_code=args.get("parent_code"),
        )
        return _account_to_dict(account)

    elif name == "list_accounts":
        at = AccountType(args["account_type"]) if "account_type" in args else None
        accounts = ledger.list_accounts(account_type=at)
        result = []
        for a in accounts:
            balance = ledger.get_account_balance(a.code)
            result.append({
                **_account_to_dict(a),
                "balance": balance.balance,
                "raw_balance": balance.raw_balance,
            })
        return result

    elif name == "get_account":
        account = ledger.get_account(args["code"])
        balance = ledger.get_account_balance(args["code"])
        return {**_account_to_dict(account), "balance_info": json.loads(balance.model_dump_json())}

    elif name == "get_account_transactions":
        return ledger.get_account_transactions(args["code"])

    elif name == "post_entry":
        lines = [
            JournalLine(
                account_code=l["account_code"],
                debit=l.get("debit", 0),
                credit=l.get("credit", 0),
            )
            for l in args["lines"]
        ]
        entry = ledger.post_entry(
            description=args["description"],
            lines=lines,
            tags=args.get("tags", []),
        )
        return _entry_to_dict(entry)

    elif name == "list_entries":
        entries = ledger.list_entries(
            account_code=args.get("account_code"),
            tag=args.get("tag"),
        )
        limit = args.get("limit", 20)
        return [_entry_to_dict(e) for e in entries[-limit:]]

    elif name == "get_entry":
        entry = ledger.get_entry(args["entry_id"])
        return _entry_to_dict(entry)

    elif name == "delete_entry":
        ledger.delete_entry(args["entry_id"])
        return {"status": "deleted", "entry_id": args["entry_id"]}

    elif name == "reconcile_entry":
        entry = ledger.reconcile_entry(args["entry_id"])
        return {"status": "reconciled", "entry_id": entry.id}

    elif name == "trial_balance":
        tb = generate_trial_balance(ledger)
        return format_trial_balance(tb)

    elif name == "income_statement":
        ist = generate_income_statement(ledger)
        return format_income_statement(ist)

    elif name == "balance_sheet":
        bs = generate_balance_sheet(ledger)
        return format_balance_sheet(bs)

    elif name == "add_exchange_rate":
        er = ledger.add_exchange_rate(
            from_currency=args["from_currency"],
            to_currency=args["to_currency"],
            rate=args["rate"],
            source=args.get("source", "manual"),
        )
        return {"status": "added", "from": er.from_currency, "to": er.to_currency, "rate": er.rate}

    elif name == "list_exchange_rates":
        converter = ledger.get_currency_converter()
        return [json.loads(r.model_dump_json()) for r in converter.list_rates()]

    # ── v0.2.0 Tools ───────────────────────────────────────────

    elif name == "close_period":
        result = close_period(
            ledger,
            retained_earnings_code=args.get("retained_earnings_code", "retained_earnings"),
            description=args.get("description"),
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

    elif name == "get_account_hierarchy":
        hierarchy = AccountHierarchy(ledger)
        tree = hierarchy.get_tree(root_code=args.get("root_code"))
        return _serialize_tree(tree)

    elif name == "get_rollup_balance":
        hierarchy = AccountHierarchy(ledger)
        rollup = hierarchy.get_rollup_balance(args["code"])
        return json.loads(rollup.model_dump_json())

    elif name == "validate_hierarchy":
        hierarchy = AccountHierarchy(ledger)
        warnings = hierarchy.validate_hierarchy()
        return {"valid": len(warnings) == 0, "warnings": warnings}

    elif name == "list_audit_log":
        action_enum = AuditAction(args["action"]) if "action" in args and args["action"] else None
        entries = ledger.audit.list_entries(
            action=action_enum,
            actor=args.get("actor"),
            limit=args.get("limit", 50),
        )
        return [json.loads(e.model_dump_json()) for e in entries]

    elif name == "export_csv":
        export_type = args["type"]
        if export_type == "accounts":
            csv_content = export_accounts_csv(ledger)
        elif export_type == "entries":
            csv_content = export_entries_csv(
                ledger,
                account_code=args.get("account_code"),
                tag=args.get("tag"),
            )
        elif export_type == "trial_balance":
            csv_content = export_trial_balance_csv(ledger)
        elif export_type == "income_statement":
            csv_content = export_income_statement_csv(ledger)
        elif export_type == "balance_sheet":
            csv_content = export_balance_sheet_csv(ledger)
        elif export_type == "hierarchy":
            csv_content = export_hierarchy_csv(ledger)
        else:
            return {"error": f"Unknown export type: {export_type}"}
        return {"format": "csv", "data": csv_content}

    elif name == "list_closed_periods":
        return ledger.get_closed_periods()

    else:
        raise LedgerError(f"Unknown tool: {name}")


def _serialize_tree(tree: list[dict]) -> list[dict]:
    """Serialize account tree to JSON-safe dicts."""
    result = []
    for node in tree:
        result.append(_serialize_tree_node(node))
    return result


def _serialize_tree_node(node: dict) -> dict:
    """Serialize a single tree node."""
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


# ── MCP Server Entry Point ─────────────────────────────────────

def run_server(ledger_path: Optional[Path] = None):
    """Run the MCP server using stdio transport."""
    import sys

    # Lazy import to avoid hard dependency on mcp package at import time
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import (
            CallToolResult, TextContent, Tool,
        )
    except ImportError:
        print("Error: 'mcp' package is required for the server. Install with: pip install mcp", file=sys.stderr)
        sys.exit(1)

    filepath = ledger_path or Path("ledger.json")
    storage = Storage(filepath)

    # Create server lazily — only load ledger when a request comes in
    _ledger: Optional[Ledger] = None

    def get_or_create_ledger() -> Ledger:
        nonlocal _ledger
        if _ledger is None:
            if storage.exists():
                _ledger = Ledger(storage)
                _ledger.reload()
            else:
                _ledger = Ledger(storage)
                _ledger._data = storage.init()
        return _ledger

    server = Server("agent-ledger")

    @server.list_tools()
    async def list_tools():
        return [Tool(**t) for t in TOOLS]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        ledger = get_or_create_ledger()
        content = handle_tool_call(ledger, name, arguments)
        return CallToolResult(
            content=[TextContent(**c) for c in content],
        )

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    import asyncio
    asyncio.run(main())
