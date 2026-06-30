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


def _parse_mcp_date(date_str: Optional[str]) -> Optional[Any]:
    """Parse an ISO 8601 date string from MCP tool arguments."""
    if date_str is None:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


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
            "properties": {
                "as_of": {"type": "string", "description": "As-of date (ISO 8601)", "default": None},
            },
        },
    },
    {
        "name": "income_statement",
        "description": "Generate an income statement (profit & loss)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date (ISO 8601)", "default": None},
                "to_date": {"type": "string", "description": "End date (ISO 8601)", "default": None},
            },
        },
    },
    {
        "name": "balance_sheet",
        "description": "Generate a balance sheet",
        "inputSchema": {
            "type": "object",
            "properties": {
                "as_of": {"type": "string", "description": "As-of date (ISO 8601)", "default": None},
            },
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
    # ── v0.3.0 New Tools ────────────────────────────────────────
    {
        "name": "reverse_entry",
        "description": "Reverse a journal entry by creating an opposing entry (debits become credits and vice versa)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "ID of the entry to reverse"},
                "reason": {"type": "string", "description": "Reason for the reversal", "default": None},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "cash_flow_statement",
        "description": "Generate a cash flow statement (operating, investing, financing activities)",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "apply_template",
        "description": "Apply a chart of accounts template (solo, startup, freelancer) to quickly set up accounts",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "enum": ["solo", "startup", "freelancer"],
                    "description": "Template to apply",
                },
            },
            "required": ["template"],
        },
    },
    {
        "name": "list_templates",
        "description": "List available chart of accounts templates",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "import_accounts_csv",
        "description": "Import accounts from CSV content. Columns: code, name, type, currency, description, parent_code",
        "inputSchema": {
            "type": "object",
            "properties": {
                "csv_content": {"type": "string", "description": "CSV content with account data"},
                "skip_errors": {"type": "boolean", "description": "Skip rows with errors", "default": False},
            },
            "required": ["csv_content"],
        },
    },
    {
        "name": "import_entries_csv",
        "description": "Import journal entries from CSV content. Columns: entry_description, account_code, debit, credit, line_description. Lines with same entry_description are grouped.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "csv_content": {"type": "string", "description": "CSV content with entry data"},
                "skip_errors": {"type": "boolean", "description": "Skip rows with errors", "default": False},
            },
            "required": ["csv_content"],
        },
    },
    # ── v0.4.0 New Tools ────────────────────────────────────────
    {
        "name": "create_bank_statement",
        "description": "Create a bank statement for reconciliation against ledger entries",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_code": {"type": "string", "description": "Account code to reconcile"},
                "closing_balance": {"type": "number", "description": "Closing balance per bank statement"},
                "opening_balance": {"type": "number", "description": "Opening balance per bank statement", "default": 0},
                "statement_date": {"type": "string", "description": "Statement date (ISO 8601)", "default": None},
                "currency": {"type": "string", "description": "Currency code", "default": "USD"},
            },
            "required": ["account_code", "closing_balance"],
        },
    },
    {
        "name": "add_bank_statement_line",
        "description": "Add a line to a bank statement for reconciliation",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
                "date": {"type": "string", "description": "Transaction date (ISO 8601)"},
                "description": {"type": "string", "description": "Transaction description", "default": ""},
                "amount": {"type": "number", "description": "Amount (positive=deposit, negative=withdrawal)"},
                "reference": {"type": "string", "description": "Check/reference number", "default": ""},
            },
            "required": ["statement_id", "amount"],
        },
    },
    {
        "name": "add_bank_statement_lines_batch",
        "description": "Add multiple lines to a bank statement. Lines: list of {date, description, amount, reference}.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string"},
                            "description": {"type": "string"},
                            "amount": {"type": "number"},
                            "reference": {"type": "string"},
                        },
                        "required": ["amount"],
                    },
                    "description": "Statement lines",
                },
            },
            "required": ["statement_id", "lines"],
        },
    },
    {
        "name": "list_bank_statements",
        "description": "List bank statements with optional filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_code": {"type": "string", "description": "Filter by account code", "default": None},
                "status": {"type": "string", "description": "Filter by status (open, in_progress, completed)", "default": None},
            },
        },
    },
    {
        "name": "get_bank_statement",
        "description": "Get details of a bank statement including all lines",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
            },
            "required": ["statement_id"],
        },
    },
    {
        "name": "match_bank_entry",
        "description": "Manually match a bank statement line to a ledger journal entry",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
                "line_id": {"type": "string", "description": "Statement line ID"},
                "entry_id": {"type": "string", "description": "Journal entry ID to match"},
            },
            "required": ["statement_id", "line_id", "entry_id"],
        },
    },
    {
        "name": "unmatch_bank_entry",
        "description": "Unmatch a previously matched bank statement line",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
                "line_id": {"type": "string", "description": "Statement line ID"},
            },
            "required": ["statement_id", "line_id"],
        },
    },
    {
        "name": "auto_match_bank_entries",
        "description": "Automatically match bank statement lines to ledger entries by amount",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
                "tolerance": {"type": "number", "description": "Amount tolerance for matching", "default": 0.01},
            },
            "required": ["statement_id"],
        },
    },
    {
        "name": "reconcile_bank_statement",
        "description": "Get reconciliation status for a bank statement (compare bank vs ledger balance)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
            },
            "required": ["statement_id"],
        },
    },
    {
        "name": "complete_bank_reconciliation",
        "description": "Complete a bank reconciliation — marks matched entries as reconciled in the ledger",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
            },
            "required": ["statement_id"],
        },
    },
    {
        "name": "dispute_bank_line",
        "description": "Mark a bank statement line as disputed",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
                "line_id": {"type": "string", "description": "Statement line ID"},
                "reason": {"type": "string", "description": "Reason for dispute", "default": None},
            },
            "required": ["statement_id", "line_id"],
        },
    },
    {
        "name": "get_unreconciled_entries",
        "description": "Get ledger entries for an account that haven't been matched to any bank statement",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_code": {"type": "string", "description": "Account code"},
            },
            "required": ["account_code"],
        },
    },
    {
        "name": "delete_bank_statement",
        "description": "Delete a bank statement",
        "inputSchema": {
            "type": "object",
            "properties": {
                "statement_id": {"type": "string", "description": "Bank statement ID"},
            },
            "required": ["statement_id"],
        },
    },
    # ── v0.5.0 New Tools ────────────────────────────────────────
    {
        "name": "tax_summary",
        "description": "Generate a tax summary report — classifies accounts into tax categories, computes taxable income and estimated tax",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date (ISO 8601)", "default": None},
                "to_date": {"type": "string", "description": "End date (ISO 8601)", "default": None},
                "tax_rate": {"type": "number", "description": "Tax rate for estimation (e.g., 0.21 for 21%)", "default": 0},
            },
        },
    },
    {
        "name": "general_ledger",
        "description": "Generate a General Ledger report — detailed journal with running balances, optionally filtered by account, date, or tag",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_code": {"type": "string", "description": "Filter by account code", "default": None},
                "from_date": {"type": "string", "description": "Start date (ISO 8601)", "default": None},
                "to_date": {"type": "string", "description": "End date (ISO 8601)", "default": None},
                "tag": {"type": "string", "description": "Filter by tag", "default": None},
            },
        },
    },
    {
        "name": "create_budget",
        "description": "Create a budget with budgeted amounts per account for a fiscal period",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Budget name (e.g., 'Q1 2024 Budget')"},
                "period_start": {"type": "string", "description": "Budget period start date (ISO 8601)", "default": None},
                "period_end": {"type": "string", "description": "Budget period end date (ISO 8601)", "default": None},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "account_code": {"type": "string"},
                            "budgeted_amount": {"type": "number"},
                        },
                        "required": ["account_code", "budgeted_amount"],
                    },
                    "description": "Budget lines with account_code and budgeted_amount",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_budgets",
        "description": "List all budgets with optional status filter",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["draft", "active", "closed"],
                    "description": "Filter by budget status",
                    "default": None,
                },
            },
        },
    },
    {
        "name": "get_budget",
        "description": "Get details of a specific budget",
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget_id": {"type": "string", "description": "Budget ID"},
            },
            "required": ["budget_id"],
        },
    },
    {
        "name": "add_budget_line",
        "description": "Add or update a budget line (account code + budgeted amount)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget_id": {"type": "string", "description": "Budget ID"},
                "account_code": {"type": "string", "description": "Account code"},
                "budgeted_amount": {"type": "number", "description": "Budgeted amount"},
            },
            "required": ["budget_id", "account_code", "budgeted_amount"],
        },
    },
    {
        "name": "remove_budget_line",
        "description": "Remove a budget line from a budget",
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget_id": {"type": "string", "description": "Budget ID"},
                "account_code": {"type": "string", "description": "Account code to remove"},
            },
            "required": ["budget_id", "account_code"],
        },
    },
    {
        "name": "activate_budget",
        "description": "Activate a draft budget",
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget_id": {"type": "string", "description": "Budget ID"},
            },
            "required": ["budget_id"],
        },
    },
    {
        "name": "close_budget",
        "description": "Close an active budget",
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget_id": {"type": "string", "description": "Budget ID"},
            },
            "required": ["budget_id"],
        },
    },
    {
        "name": "budget_variance_report",
        "description": "Generate a budget variance report comparing budgeted vs actual amounts",
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget_id": {"type": "string", "description": "Budget ID"},
            },
            "required": ["budget_id"],
        },
    },
    {
        "name": "create_fiscal_year",
        "description": "Create a fiscal year with automatic period generation (monthly or quarterly)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Fiscal year name (e.g., 'FY 2024')"},
                "start_date": {"type": "string", "description": "Start date (ISO 8601)"},
                "end_date": {"type": "string", "description": "End date (ISO 8601)"},
                "auto_periods": {"type": "boolean", "description": "Auto-generate periods", "default": True},
                "period_type": {"type": "string", "enum": ["month", "quarter"], "description": "Period type", "default": "month"},
            },
            "required": ["name", "start_date", "end_date"],
        },
    },
    {
        "name": "list_fiscal_years",
        "description": "List all fiscal years",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "closed"],
                    "description": "Filter by status",
                    "default": None,
                },
            },
        },
    },
    {
        "name": "close_fiscal_year",
        "description": "Close a fiscal year and all its periods",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fy_id": {"type": "string", "description": "Fiscal year ID"},
            },
            "required": ["fy_id"],
        },
    },
    {
        "name": "get_active_fiscal_year",
        "description": "Get the currently active fiscal year (the one containing today's date)",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "delete_budget",
        "description": "Delete a draft budget",
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget_id": {"type": "string", "description": "Budget ID"},
            },
            "required": ["budget_id"],
        },
    },
    # ── v0.6.0 New Tools ────────────────────────────────────────
    {
        "name": "search_entries",
        "description": "Search journal entries by description text (case-insensitive). Optionally filter by account code or tag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search string to match against entry descriptions"},
                "account_code": {"type": "string", "description": "Optional filter by account code", "default": None},
                "tag": {"type": "string", "description": "Optional filter by tag", "default": None},
                "limit": {"type": "integer", "description": "Max entries to return", "default": 50},
            },
            "required": ["query"],
        },
    },
    {
        "name": "update_account",
        "description": "Update an existing account's name, description, active status, or tags",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Account code"},
                "name": {"type": "string", "description": "New account name", "default": None},
                "description": {"type": "string", "description": "New description", "default": None},
                "active": {"type": "boolean", "description": "Set active status", "default": None},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New tags (replaces existing)",
                    "default": None,
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "add_account_tag",
        "description": "Add a tag to an account for filtering and grouping",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Account code"},
                "tag": {"type": "string", "description": "Tag to add"},
            },
            "required": ["code", "tag"],
        },
    },
    {
        "name": "remove_account_tag",
        "description": "Remove a tag from an account",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Account code"},
                "tag": {"type": "string", "description": "Tag to remove"},
            },
            "required": ["code", "tag"],
        },
    },
    {
        "name": "list_accounts_by_tag",
        "description": "List accounts that have a specific tag",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag to filter by"},
            },
            "required": ["tag"],
        },
    },

    # ── v0.7.0: Recurring Entries ──────────────────────────────

    {
        "name": "create_recurring_entry",
        "description": (
            "Create a recurring journal entry template that auto-generates entries "
            "on a schedule (daily, weekly, monthly, quarterly, yearly). Lines must balance."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name"},
                "description": {"type": "string", "description": "Description for generated entries"},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "account_code": {"type": "string"},
                            "debit": {"type": "number"},
                            "credit": {"type": "number"},
                        },
                        "required": ["account_code"],
                    },
                    "minItems": 2,
                    "description": "Template lines (must balance)",
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly", "quarterly", "yearly"],
                    "description": "Schedule type",
                },
                "interval": {"type": "integer", "description": "Every N periods (default 1)"},
                "day_of_month": {"type": "integer", "description": "Day of month (1-31)"},
                "day_of_week": {"type": "integer", "description": "Weekday 0=Mon"},
                "month_of_year": {"type": "integer", "description": "Month 1-12 for yearly"},
                "start_date": {"type": "string", "description": "ISO 8601 start date"},
                "end_date": {"type": "string", "description": "ISO 8601 end date"},
                "max_occurrences": {"type": "integer", "description": "Max entries to generate"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "description", "lines"],
        },
    },
    {
        "name": "list_recurring_entries",
        "description": "List recurring entry templates, optionally filtered to active only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean", "description": "Only show active templates"},
            },
        },
    },
    {
        "name": "get_recurring_entry",
        "description": "Get details of a specific recurring entry template.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Template ID"},
            },
            "required": ["template_id"],
        },
    },
    {
        "name": "pause_recurring_entry",
        "description": "Pause a recurring entry template (stops generation).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
            },
            "required": ["template_id"],
        },
    },
    {
        "name": "resume_recurring_entry",
        "description": "Resume a paused recurring entry template.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
            },
            "required": ["template_id"],
        },
    },
    {
        "name": "delete_recurring_entry",
        "description": "Delete a recurring entry template.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
            },
            "required": ["template_id"],
        },
    },
    {
        "name": "process_recurring_entries",
        "description": (
            "Process all due recurring templates — generates journal entries for "
            "any template whose next_run is in the past. Use this to catch up on "
            "scheduled entries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },

    # ── v0.7.0: Financial Ratios ───────────────────────────────

    {
        "name": "financial_ratios",
        "description": (
            "Compute standard financial ratios and KPIs: current ratio, quick ratio, "
            "cash ratio, debt-to-equity, debt-to-assets, profit margin, ROA, ROE, "
            "operating margin, asset turnover, working capital."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "as_of": {"type": "string", "description": "ISO 8601 date for point-in-time ratios"},
                "cash_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for cash accounts"},
                "inventory_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for inventory accounts"},
                "current_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for current accounts"},
            },
        },
    },
    {
        "name": "financial_health",
        "description": (
            "Assess financial health across liquidity, solvency, and profitability. "
            "Returns status indicators (healthy/adequate/at_risk) for each category."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "as_of": {"type": "string", "description": "ISO 8601 date"},
            },
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
        as_of = _parse_mcp_date(args.get("as_of"))
        tb = generate_trial_balance(ledger, as_of=as_of)
        return format_trial_balance(tb)

    elif name == "income_statement":
        from_date = _parse_mcp_date(args.get("from_date"))
        to_date = _parse_mcp_date(args.get("to_date"))
        ist = generate_income_statement(ledger, from_date=from_date, to_date=to_date)
        return format_income_statement(ist)

    elif name == "balance_sheet":
        as_of = _parse_mcp_date(args.get("as_of"))
        bs = generate_balance_sheet(ledger, as_of=as_of)
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

    # ── v0.3.0 Tools ───────────────────────────────────────────

    elif name == "reverse_entry":
        reversal = ledger.reverse_entry(
            entry_id=args["entry_id"],
            reason=args.get("reason"),
        )
        return {
            "status": "reversed",
            "original_entry_id": args["entry_id"],
            "reversal_entry_id": reversal.id,
            "reversal_description": reversal.description,
        }

    elif name == "cash_flow_statement":
        from .cashflow import generate_cash_flow_statement, format_cash_flow_statement
        cf = generate_cash_flow_statement(ledger)
        return format_cash_flow_statement(cf)

    elif name == "apply_template":
        from .templates import apply_template
        created = apply_template(ledger, args["template"])
        return {
            "status": "applied",
            "template": args["template"],
            "accounts_created": len(created),
            "account_codes": [a.code for a in created],
        }

    elif name == "list_templates":
        from .templates import get_template_names
        return get_template_names()

    elif name == "import_accounts_csv":
        from .import_csv import import_accounts_csv
        result = import_accounts_csv(
            ledger,
            csv_content=args["csv_content"],
            skip_errors=args.get("skip_errors", False),
        )
        return {
            "imported": result.imported,
            "skipped": result.skipped,
            "errors": result.errors,
        }

    elif name == "import_entries_csv":
        from .import_csv import import_entries_csv
        result = import_entries_csv(
            ledger,
            csv_content=args["csv_content"],
            skip_errors=args.get("skip_errors", False),
        )
        return {
            "imported": result.imported,
            "skipped": result.skipped,
            "errors": result.errors,
        }

    # ── v0.4.0 Tools ───────────────────────────────────────────

    elif name == "create_bank_statement":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        sd = _parse_mcp_date(args.get("statement_date"))
        stmt = recon_obj.create_statement(
            account_code=args["account_code"],
            closing_balance=args["closing_balance"],
            opening_balance=args.get("opening_balance", 0),
            statement_date=sd,
            currency=args.get("currency", "USD"),
        )
        return {
            "statement_id": stmt.id,
            "account_code": stmt.account_code,
            "closing_balance": stmt.closing_balance,
            "status": stmt.status,
        }

    elif name == "add_bank_statement_line":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        dt = _parse_mcp_date(args.get("date"))
        line = recon_obj.add_statement_line(
            statement_id=args["statement_id"],
            date=dt,
            description=args.get("description", ""),
            amount=args["amount"],
            reference=args.get("reference", ""),
        )
        return {"line_id": line.id, "amount": line.amount, "status": line.status}

    elif name == "add_bank_statement_lines_batch":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        created = recon_obj.add_statement_lines_batch(
            statement_id=args["statement_id"],
            lines=args["lines"],
        )
        return {"created": len(created), "line_ids": [l.id for l in created]}

    elif name == "list_bank_statements":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        statements = recon_obj.list_statements(
            account_code=args.get("account_code"),
            status=args.get("status"),
        )
        return [
            {
                "id": s.id,
                "account_code": s.account_code,
                "statement_date": s.statement_date.isoformat() if s.statement_date else None,
                "opening_balance": s.opening_balance,
                "closing_balance": s.closing_balance,
                "lines": len(s.lines),
                "status": s.status,
            }
            for s in statements
        ]

    elif name == "get_bank_statement":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        stmt = recon_obj.get_statement(args["statement_id"])
        return {
            "id": stmt.id,
            "account_code": stmt.account_code,
            "statement_date": stmt.statement_date.isoformat() if stmt.statement_date else None,
            "opening_balance": stmt.opening_balance,
            "closing_balance": stmt.closing_balance,
            "currency": stmt.currency,
            "status": stmt.status,
            "lines": [
                {
                    "id": l.id,
                    "date": l.date.isoformat() if l.date else None,
                    "description": l.description,
                    "amount": l.amount,
                    "reference": l.reference,
                    "matched_entry_id": l.matched_entry_id,
                    "status": l.status,
                }
                for l in stmt.lines
            ],
        }

    elif name == "match_bank_entry":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        line = recon_obj.match_entry(args["statement_id"], args["line_id"], args["entry_id"])
        return {"status": "matched", "line_id": line.id, "matched_entry_id": line.matched_entry_id}

    elif name == "unmatch_bank_entry":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        line = recon_obj.unmatch_entry(args["statement_id"], args["line_id"])
        return {"status": "unmatched", "line_id": line.id}

    elif name == "auto_match_bank_entries":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        result = recon_obj.auto_match(args["statement_id"], tolerance=args.get("tolerance", 0.01))
        return result

    elif name == "reconcile_bank_statement":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        result = recon_obj.reconcile(args["statement_id"])
        return {
            "statement_id": result.statement_id,
            "total_statement_lines": result.total_statement_lines,
            "matched": result.matched,
            "unmatched_statement": result.unmatched_statement,
            "disputed": result.disputed,
            "statement_closing_balance": result.statement_closing_balance,
            "ledger_balance": result.ledger_balance,
            "difference": result.difference,
            "is_balanced": result.is_balanced,
        }

    elif name == "complete_bank_reconciliation":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        result = recon_obj.complete_reconciliation(args["statement_id"])
        return {
            "status": "completed",
            "matched": result.matched,
            "difference": result.difference,
            "is_balanced": result.is_balanced,
        }

    elif name == "dispute_bank_line":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        line = recon_obj.mark_disputed(args["statement_id"], args["line_id"], reason=args.get("reason"))
        return {"status": "disputed", "line_id": line.id}

    elif name == "get_unreconciled_entries":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        return recon_obj.get_unreconciled_entries(args["account_code"])

    elif name == "delete_bank_statement":
        from .reconciliation import BankReconciliation
        recon_obj = BankReconciliation(ledger)
        recon_obj.delete_statement(args["statement_id"])
        return {"deleted": True, "statement_id": args["statement_id"]}

    # ── v0.5.0 Tools ───────────────────────────────────────────

    elif name == "tax_summary":
        from .tax import generate_tax_summary, format_tax_summary
        from_date = _parse_mcp_date(args.get("from_date"))
        to_date = _parse_mcp_date(args.get("to_date"))
        tax_rate = float(args.get("tax_rate", 0))
        report = generate_tax_summary(
            ledger,
            from_date=from_date,
            to_date=to_date,
            tax_rate=tax_rate,
        )
        return format_tax_summary(report)

    elif name == "general_ledger":
        from .general_ledger import generate_general_ledger, format_general_ledger
        from_date = _parse_mcp_date(args.get("from_date"))
        to_date = _parse_mcp_date(args.get("to_date"))
        report = generate_general_ledger(
            ledger,
            account_code=args.get("account_code"),
            from_date=from_date,
            to_date=to_date,
            tag=args.get("tag"),
        )
        return format_general_ledger(report)

    elif name == "create_budget":
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        ps = _parse_mcp_date(args.get("period_start"))
        pe = _parse_mcp_date(args.get("period_end"))
        budget = bm.create_budget(
            name=args["name"],
            period_start=ps,
            period_end=pe,
            budget_lines=args.get("lines"),
        )
        return {
            "id": budget.id,
            "name": budget.name,
            "status": budget.status,
            "lines": len(budget.lines),
            "total_budgeted": budget.total_budgeted,
        }

    elif name == "list_budgets":
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        budgets = bm.list_budgets(status=args.get("status"))
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

    elif name == "get_budget":
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        budget = bm.get_budget(args["budget_id"])
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

    elif name == "add_budget_line":
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        line = bm.add_budget_line(
            budget_id=args["budget_id"],
            account_code=args["account_code"],
            budgeted_amount=args["budgeted_amount"],
        )
        return {
            "account_code": line.account_code,
            "budgeted_amount": line.budgeted_amount,
            "status": "added",
        }

    elif name == "remove_budget_line":
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        bm.remove_budget_line(
            budget_id=args["budget_id"],
            account_code=args["account_code"],
        )
        return {"status": "removed", "account_code": args["account_code"]}

    elif name == "activate_budget":
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        budget = bm.activate_budget(args["budget_id"])
        return {"id": budget.id, "status": budget.status}

    elif name == "close_budget":
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        budget = bm.close_budget(args["budget_id"])
        return {"id": budget.id, "status": budget.status}

    elif name == "budget_variance_report":
        from .budget import BudgetManager, format_variance_report
        bm = BudgetManager(ledger)
        report = bm.get_variance_report(args["budget_id"])
        return format_variance_report(report)

    elif name == "create_fiscal_year":
        from .fiscal import FiscalYearManager
        fm = FiscalYearManager(ledger)
        start = _parse_mcp_date(args["start_date"])
        end = _parse_mcp_date(args["end_date"])
        if start is None or end is None:
            return {"error": "start_date and end_date are required"}
        fy = fm.create_fiscal_year(
            name=args["name"],
            start_date=start,
            end_date=end,
            auto_periods=args.get("auto_periods", True),
            period_type=args.get("period_type", "month"),
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

    elif name == "list_fiscal_years":
        from .fiscal import FiscalYearManager
        fm = FiscalYearManager(ledger)
        years = fm.list_fiscal_years(status=args.get("status"))
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

    elif name == "close_fiscal_year":
        from .fiscal import FiscalYearManager
        fm = FiscalYearManager(ledger)
        fy = fm.close_fiscal_year(args["fy_id"])
        return {"id": fy.id, "name": fy.name, "status": fy.status}

    elif name == "get_active_fiscal_year":
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
            "periods": len(fy.periods),
        }

    elif name == "delete_budget":
        from .budget import BudgetManager
        bm = BudgetManager(ledger)
        bm.delete_budget(args["budget_id"])
        return {"deleted": True, "budget_id": args["budget_id"]}

    # ── v0.6.0 Tools ────────────────────────────────────────────
    elif name == "search_entries":
        results = ledger.search_entries(
            query=args["query"],
            account_code=args.get("account_code"),
            tag=args.get("tag"),
            limit=args.get("limit", 50),
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

    elif name == "update_account":
        account = ledger.update_account(
            code=args["code"],
            name=args.get("name"),
            description=args.get("description"),
            active=args.get("active"),
            tags=args.get("tags"),
        )
        return {
            "code": account.code,
            "name": account.name,
            "description": account.description,
            "active": account.active,
            "tags": account.tags,
        }

    elif name == "add_account_tag":
        account = ledger.get_account(args["code"])
        if args["tag"] not in account.tags:
            account.tags.append(args["tag"])
            ledger.save()
        return {
            "code": account.code,
            "name": account.name,
            "tags": account.tags,
        }

    elif name == "remove_account_tag":
        account = ledger.get_account(args["code"])
        if args["tag"] in account.tags:
            account.tags.remove(args["tag"])
            ledger.save()
        return {
            "code": account.code,
            "name": account.name,
            "tags": account.tags,
        }

    elif name == "list_accounts_by_tag":
        accounts = ledger.list_accounts(tag=args["tag"])
        return [
            {
                "code": a.code,
                "name": a.name,
                "account_type": a.account_type.value,
                "active": a.active,
                "tags": a.tags,
            }
            for a in accounts
        ]

    # ── v0.7.0: Recurring Entries ──────────────────────────────

    elif name == "create_recurring_entry":
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        sd = _parse_mcp_date(args.get("start_date"))
        ed = _parse_mcp_date(args.get("end_date"))
        template = rm.create(
            name=args["name"],
            description=args["description"],
            lines=args["lines"],
            schedule_type=args.get("schedule_type", "monthly"),
            interval=args.get("interval", 1),
            day_of_month=args.get("day_of_month", 1),
            day_of_week=args.get("day_of_week", 0),
            month_of_year=args.get("month_of_year", 1),
            start_date=sd,
            end_date=ed,
            max_occurrences=args.get("max_occurrences"),
            tags=args.get("tags"),
        )
        return {
            "id": template.id,
            "name": template.name,
            "schedule_type": template.schedule_type.value,
            "interval": template.interval,
            "active": template.active,
            "next_run": template.next_run.isoformat() if template.next_run else None,
            "lines": len(template.lines),
        }

    elif name == "list_recurring_entries":
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        templates = rm.list_templates(active_only=args.get("active_only", False))
        return [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "schedule_type": t.schedule_type.value,
                "interval": t.interval,
                "active": t.active,
                "occurrences_created": t.occurrences_created,
                "last_run": t.last_run.isoformat() if t.last_run else None,
                "next_run": t.next_run.isoformat() if t.next_run else None,
            }
            for t in templates
        ]

    elif name == "get_recurring_entry":
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        t = rm.get(args["template_id"])
        return {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "schedule_type": t.schedule_type.value,
            "interval": t.interval,
            "day_of_month": t.day_of_month,
            "day_of_week": t.day_of_week,
            "month_of_year": t.month_of_year,
            "start_date": t.start_date.isoformat() if t.start_date else None,
            "end_date": t.end_date.isoformat() if t.end_date else None,
            "max_occurrences": t.max_occurrences,
            "active": t.active,
            "occurrences_created": t.occurrences_created,
            "last_run": t.last_run.isoformat() if t.last_run else None,
            "next_run": t.next_run.isoformat() if t.next_run else None,
            "tags": t.tags,
            "lines": [
                {
                    "account_code": l.account_code,
                    "debit": l.debit,
                    "credit": l.credit,
                    "description": l.description,
                }
                for l in t.lines
            ],
        }

    elif name == "pause_recurring_entry":
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        t = rm.pause(args["template_id"])
        return {"id": t.id, "active": t.active}

    elif name == "resume_recurring_entry":
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        t = rm.resume(args["template_id"])
        return {"id": t.id, "active": t.active}

    elif name == "delete_recurring_entry":
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        rm.delete(args["template_id"])
        return {"deleted": True, "template_id": args["template_id"]}

    elif name == "process_recurring_entries":
        from .recurring import RecurringManager
        rm = RecurringManager(ledger)
        results = rm.process_all()
        return {
            "processed": len(results),
            "generated": sum(1 for r in results if r["status"] == "generated"),
            "results": results,
        }

    # ── v0.7.0: Financial Ratios ───────────────────────────────

    elif name == "financial_ratios":
        from .ratios import compute_ratios, format_ratios
        as_of = _parse_mcp_date(args.get("as_of"))
        ratios = compute_ratios(
            ledger,
            as_of=as_of,
            cash_tags=set(args["cash_tags"]) if "cash_tags" in args else None,
            inventory_tags=set(args["inventory_tags"]) if "inventory_tags" in args else None,
            current_tags=set(args["current_tags"]) if "current_tags" in args else None,
        )
        return format_ratios(ratios)

    elif name == "financial_health":
        from .ratios import compute_ratios, get_financial_health, format_ratios
        as_of = _parse_mcp_date(args.get("as_of"))
        ratios = compute_ratios(ledger, as_of=as_of)
        health = get_financial_health(ratios)
        return {
            "health": health,
            "summary": {
                "total_assets": ratios.total_assets,
                "total_liabilities": ratios.total_liabilities,
                "total_equity": ratios.total_equity,
                "net_income": ratios.net_income,
                "working_capital": ratios.working_capital,
            },
            "warnings": ratios.warnings,
        }

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
