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
        "name": "reverse_entry",
        "description": "Reverse a journal entry by creating a counterbalancing entry",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "ID of the entry to reverse"},
                "reason": {"type": "string", "description": "Reason for reversal", "default": ""},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "cash_flow_statement",
        "description": "Generate a cash flow statement (indirect method) showing operating, investing, and financing activities",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date (ISO 8601)", "default": None},
                "to_date": {"type": "string", "description": "End date (ISO 8601)", "default": None},
            },
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
    # ── v0.3.0 Wallet Tools ──────────────────────────────────────
    {
        "name": "wallet_connect",
        "description": "Connect a Solana wallet and check its balance",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Solana wallet address (base58)",
                },
                "network": {
                    "type": "string",
                    "enum": ["mainnet", "devnet"],
                    "description": "Solana network",
                    "default": "mainnet",
                },
            },
            "required": ["address"],
        },
    },
    {
        "name": "wallet_sync",
        "description": "Sync Solana wallet transactions into the ledger as journal entries. "
                       "Fetches on-chain transactions, categorizes them, and creates double-entry journal entries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Solana wallet address",
                },
                "network": {
                    "type": "string",
                    "enum": ["mainnet", "devnet"],
                    "description": "Solana network",
                    "default": "mainnet",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max transactions to fetch",
                    "default": 50,
                },
                "create_accounts": {
                    "type": "boolean",
                    "description": "Auto-create missing ledger accounts",
                    "default": True,
                },
                "import_fees": {
                    "type": "boolean",
                    "description": "Create separate fee entries",
                    "default": True,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Categorize without posting entries",
                    "default": False,
                },
            },
            "required": ["address"],
        },
    },
    {
        "name": "wallet_status",
        "description": "Get wallet sync status and current balance",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Solana wallet address",
                },
                "network": {
                    "type": "string",
                    "enum": ["mainnet", "devnet"],
                    "description": "Solana network",
                    "default": "mainnet",
                },
            },
            "required": ["address"],
        },
    },
    {
        "name": "wallet_setup_accounts",
        "description": "Create the default chart of accounts for Solana wallet tracking (sol_wallet, sol_income, sol_expense, network_fees, etc.)",
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
    {
        "name": "aging_report",
        "description": (
            "Generate an AR or AP aging report showing outstanding balances "
            "bucketed by age (0-30, 31-60, 61-90, 90+ days). Uses FIFO to "
            "apply payments to the oldest entries first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Account codes (e.g. ['ar'] or ['ap'])",
                },
                "report_type": {
                    "type": "string",
                    "enum": ["receivable", "payable"],
                    "description": "Type of aging report",
                },
                "as_of": {"type": "string", "description": "ISO 8601 date for the report"},
            },
            "required": ["account_codes"],
        },
    },
    {
        "name": "create_fixed_asset",
        "description": (
            "Register a fixed asset for depreciation tracking. "
            "Supports straight-line, declining balance, double-declining, "
            "and units-of-production methods."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "asset_account": {"type": "string", "description": "Account code for the asset"},
                "accum_dep_account": {"type": "string", "description": "Accumulated depreciation account"},
                "dep_expense_account": {"type": "string", "description": "Depreciation expense account"},
                "cost": {"type": "number", "description": "Acquisition cost"},
                "salvage_value": {"type": "number", "description": "Estimated salvage value"},
                "useful_life_months": {"type": "integer", "description": "Useful life in months"},
                "method": {
                    "type": "string",
                    "enum": ["straight_line", "declining_balance", "double_declining", "units_of_production"],
                },
                "declining_rate": {"type": "number", "description": "Custom declining balance rate"},
                "total_units": {"type": "number", "description": "Total units for units-of-production"},
                "description": {"type": "string"},
            },
            "required": ["name", "asset_account", "accum_dep_account", "dep_expense_account", "cost"],
        },
    },
    {
        "name": "list_fixed_assets",
        "description": "List all fixed assets, optionally filtered to active only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean"},
            },
        },
    },
    {
        "name": "get_fixed_asset",
        "description": "Get details of a specific fixed asset including depreciation history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "post_depreciation",
        "description": (
            "Post one period of depreciation for a fixed asset. "
            "Creates a journal entry: Dr Depreciation Expense, Cr Accumulated Depreciation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "post_all_depreciation",
        "description": (
            "Post depreciation for all active, non-fully-depreciated assets. "
            "Returns a summary of amounts posted per asset."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "depreciation_schedule",
        "description": "Get the projected depreciation schedule for an asset.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "periods": {"type": "integer", "description": "Number of periods to project"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "dispose_asset",
        "description": (
            "Dispose of a fixed asset. Records the removal of accumulated "
            "depreciation and asset cost, plus any gain or loss on disposal."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "disposal_value": {"type": "number", "description": "Amount received from disposal"},
                "disposal_account": {"type": "string", "description": "Account to debit for proceeds"},
            },
            "required": ["asset_id"],
        },
    },
    # ── Cost Center tools ──────────────────────────────────────
    {
        "name": "create_cost_center",
        "description": (
            "Create a cost center, profit center, or project for dimensional "
            "accounting. Enables tracking revenue and expenses by project, "
            "department, or any business dimension."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Unique code (e.g. 'proj-alpha')"},
                "name": {"type": "string", "description": "Human-readable name"},
                "center_type": {
                    "type": "string",
                    "enum": ["cost", "profit", "project", "department", "investment"],
                    "description": "Center type",
                    "default": "cost",
                },
                "description": {"type": "string", "description": "Description", "default": ""},
                "parent_code": {"type": "string", "description": "Parent cost center code for hierarchy", "default": None},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags", "default": []},
            },
            "required": ["code", "name"],
        },
    },
    {
        "name": "list_cost_centers",
        "description": "List all cost centers, optionally filtered by type or active status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "center_type": {
                    "type": "string",
                    "enum": ["cost", "profit", "project", "department", "investment"],
                    "description": "Filter by type",
                },
                "active_only": {"type": "boolean", "description": "Show only active centers", "default": False},
            },
        },
    },
    {
        "name": "get_cost_center",
        "description": "Get details of a specific cost center.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Cost center code"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "update_cost_center",
        "description": "Update a cost center's name, description, active status, or tags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Cost center code"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "active": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["code"],
        },
    },
    {
        "name": "delete_cost_center",
        "description": "Delete a cost center. Fails if it has children or entries assigned.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Cost center code"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "assign_entry_to_cost_center",
        "description": (
            "Assign a journal entry to a cost center. This enables dimensional "
            "reporting — all lines in the entry will be attributed to the specified "
            "cost center for profitability analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "Journal entry ID"},
                "cost_center_code": {"type": "string", "description": "Cost center code"},
            },
            "required": ["entry_id", "cost_center_code"],
        },
    },
    {
        "name": "unassign_entry_from_cost_center",
        "description": "Remove cost center assignment from a journal entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "Journal entry ID"},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "cost_center_report",
        "description": (
            "Generate a financial report for a specific cost center. Shows all "
            "account activity (revenue, expenses, assets) for entries assigned "
            "to the center, with totals and net income."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Cost center code"},
                "from_date": {"type": "string", "description": "Start date (ISO 8601)", "default": None},
                "to_date": {"type": "string", "description": "End date (ISO 8601)", "default": None},
            },
            "required": ["code"],
        },
    },
    {
        "name": "cost_center_summary",
        "description": (
            "Generate a summary of all cost centers with revenue, expenses, "
            "and net income. Also shows unassigned entry totals. Useful for "
            "comparing profitability across projects or departments."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date (ISO 8601)", "default": None},
                "to_date": {"type": "string", "description": "End date (ISO 8601)", "default": None},
            },
        },
    },
    {
        "name": "cost_center_hierarchy",
        "description": "Get the cost center hierarchy as a tree structure.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "parent_code": {"type": "string", "description": "Get subtree under this parent (default: full tree)", "default": None},
            },
        },
    },
    # ── Period Comparison tools ────────────────────────────────
    {
        "name": "compare_account_balances",
        "description": (
            "Compare account balances across multiple time periods. Shows "
            "each account's value in each period, variance, and percentage "
            "change. Useful for trend analysis and period-over-period comparisons."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "periods": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from_date": {"type": "string", "description": "Start date (ISO 8601) or null"},
                            "to_date": {"type": "string", "description": "End date (ISO 8601) or null"},
                            "label": {"type": "string", "description": "Period label (e.g. 'Q1 2024')"},
                        },
                        "required": ["label"],
                    },
                    "description": "At least 2 periods to compare",
                    "minItems": 2,
                },
            },
            "required": ["periods"],
        },
    },
    {
        "name": "compare_income_statements",
        "description": (
            "Compare income statements across multiple time periods. Shows "
            "revenue, expenses, and net income side-by-side with variance "
            "and percentage change. Essential for tracking profitability trends."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "periods": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from_date": {"type": "string", "description": "Start date (ISO 8601) or null"},
                            "to_date": {"type": "string", "description": "End date (ISO 8601) or null"},
                            "label": {"type": "string", "description": "Period label"},
                        },
                        "required": ["label"],
                    },
                    "description": "At least 2 periods to compare",
                    "minItems": 2,
                },
            },
            "required": ["periods"],
        },
    },
    # ── v1.0.0 New Tools ──────────────────────────────────────────
    {
        "name": "create_alert_rule",
        "description": (
            "Create a balance monitoring alert rule. The rule fires when an "
            "account balance crosses a threshold (above, below, equals, or changed). "
            "Useful for monitoring cash reserves, debt levels, or budget limits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Rule name"},
                "account_code": {"type": "string", "description": "Account to monitor"},
                "condition": {
                    "type": "string",
                    "enum": ["above", "below", "equals", "changed"],
                    "description": "Trigger condition",
                },
                "threshold": {"type": "number", "description": "Threshold value"},
                "severity": {
                    "type": "string",
                    "enum": ["info", "warning", "critical"],
                    "description": "Alert severity",
                    "default": "warning",
                },
                "description": {"type": "string", "description": "Optional description", "default": ""},
                "cooldown_minutes": {
                    "type": "integer",
                    "description": "Min minutes between triggers of same rule",
                    "default": 60,
                },
            },
            "required": ["name", "account_code", "condition", "threshold"],
        },
    },
    {
        "name": "list_alert_rules",
        "description": "List all balance alert rules with optional filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_code": {"type": "string", "description": "Filter by account"},
                "enabled_only": {"type": "boolean", "description": "Only enabled rules", "default": False},
            },
        },
    },
    {
        "name": "delete_alert_rule",
        "description": "Delete a balance alert rule",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "Rule ID to delete"},
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "check_alerts",
        "description": (
            "Check all enabled alert rules against current balances. Returns "
            "triggered alerts. Rules within their cooldown period are not re-triggered."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_alert_triggers",
        "description": "List triggered alerts with optional filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "Filter by rule ID"},
                "acknowledged": {"type": "boolean", "description": "Filter by acknowledgment status"},
                "limit": {"type": "integer", "description": "Max triggers", "default": 50},
            },
        },
    },
    {
        "name": "acknowledge_alert",
        "description": "Mark a triggered alert as acknowledged",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trigger_id": {"type": "string", "description": "Trigger ID to acknowledge"},
            },
            "required": ["trigger_id"],
        },
    },
    {
        "name": "create_api_key",
        "description": (
            "Create a new API key for REST API access. Returns the raw key "
            "which should be stored securely — it won't be shown again."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Key name"},
                "scopes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["read", "write", "reports", "admin", "reconcile", "budget"],
                    },
                    "description": "Permission scopes",
                    "default": ["read"],
                },
                "description": {"type": "string", "description": "Key description", "default": ""},
                "rate_limit_per_hour": {
                    "type": "integer",
                    "description": "Optional rate limit",
                    "default": None,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_api_keys",
        "description": "List all API keys (raw key values are not shown)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean", "description": "Only active keys", "default": False},
            },
        },
    },
    {
        "name": "revoke_api_key",
        "description": "Revoke an API key (deactivates it)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key_id": {"type": "string", "description": "Key ID to revoke"},
            },
            "required": ["key_id"],
        },
    },
    {
        "name": "generate_dashboard",
        "description": (
            "Generate a complete HTML financial dashboard with balance sheet, "
            "income statement, trial balance, financial ratios, and active alerts. "
            "The dashboard is self-contained (no external dependencies) and can be "
            "saved to a file or served via the REST API."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Dashboard title",
                    "default": "Agent Ledger Dashboard",
                },
                "include_alerts": {
                    "type": "boolean",
                    "description": "Include alerts section",
                    "default": True,
                },
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

    # ── v0.3.0 Wallet Tools ──────────────────────────────────────

    elif name == "wallet_connect":
        from .importer import WalletImporter
        importer = WalletImporter(ledger=ledger)
        try:
            info = importer.connect_wallet(
                args["address"],
                network=args.get("network", "mainnet"),
            )
            return {
                "address": info.address,
                "network": info.network,
                "sol_balance": info.sol_balance,
                "lamports": info.lamports,
                "transactions_imported": info.transaction_count,
                "last_synced_at": info.last_synced_at.isoformat() if info.last_synced_at else None,
            }
        except Exception as e:
            return {"error": str(e)}
        finally:
            importer.close()

    elif name == "wallet_sync":
        from .importer import WalletImporter
        importer = WalletImporter(ledger=ledger)
        try:
            result = importer.sync_wallet(
                wallet_address=args["address"],
                network=args.get("network", "mainnet"),
                limit=args.get("limit", 50),
                create_accounts=args.get("create_accounts", True),
                import_fees=args.get("import_fees", True),
                dry_run=args.get("dry_run", False),
            )
            return {
                "wallet_address": result.wallet_address,
                "transactions_fetched": result.transactions_fetched,
                "transactions_imported": result.transactions_imported,
                "transactions_skipped": result.transactions_skipped,
                "transactions_failed": result.transactions_failed,
                "total_sol_imported": result.total_sol_imported,
                "entries_created": result.entries_created,
                "errors": result.errors,
            }
        except Exception as e:
            return {"error": str(e)}
        finally:
            importer.close()

    elif name == "wallet_status":
        from .importer import WalletImporter
        importer = WalletImporter(ledger=ledger)
        try:
            info = importer.get_wallet_info(
                args["address"],
                network=args.get("network", "mainnet"),
            )
            return {
                "address": info.address,
                "network": info.network,
                "sol_balance": info.sol_balance,
                "lamports": info.lamports,
                "last_synced_slot": info.last_synced_slot,
                "last_synced_at": info.last_synced_at.isoformat() if info.last_synced_at else None,
                "transaction_count": info.transaction_count,
            }
        except Exception as e:
            return {"error": str(e)}
        finally:
            importer.close()

    elif name == "wallet_setup_accounts":
        from .importer import WalletImporter
        importer = WalletImporter(ledger=ledger)
        try:
            created = importer.setup_wallet_accounts()
            return {
                "accounts_created": created,
                "count": len(created),
            }
        finally:
            importer.close()

    # ── v1.0.0: Alerts ────────────────────────────────────────────

    elif name == "create_alert_rule":
        from .alerts import AlertManager, AlertCondition, AlertSeverity
        am = AlertManager(ledger)
        rule = am.create_rule(
            name=args["name"],
            account_code=args["account_code"],
            condition=AlertCondition(args["condition"]),
            threshold=args["threshold"],
            severity=AlertSeverity(args.get("severity", "warning")),
            description=args.get("description", ""),
            cooldown_minutes=args.get("cooldown_minutes", 60),
        )
        return {
            "id": rule.id, "name": rule.name, "account_code": rule.account_code,
            "condition": rule.condition.value, "threshold": rule.threshold,
            "severity": rule.severity.value, "enabled": rule.enabled,
        }

    elif name == "list_alert_rules":
        from .alerts import AlertManager
        am = AlertManager(ledger)
        rules = am.list_rules(
            account_code=args.get("account_code"),
            enabled_only=args.get("enabled_only", False),
        )
        return [
            {"id": r.id, "name": r.name, "account_code": r.account_code,
             "condition": r.condition.value, "threshold": r.threshold,
             "severity": r.severity.value, "enabled": r.enabled}
            for r in rules
        ]

    elif name == "delete_alert_rule":
        from .alerts import AlertManager
        am = AlertManager(ledger)
        am.delete_rule(args["rule_id"])
        return {"deleted": True, "rule_id": args["rule_id"]}

    elif name == "check_alerts":
        from .alerts import AlertManager
        am = AlertManager(ledger)
        triggers = am.check_rules()
        return {
            "checked": True, "triggered": len(triggers),
            "triggers": [
                {"id": t.id, "rule_name": t.rule_name, "account_code": t.account_code,
                 "severity": t.severity, "message": t.message,
                 "actual_value": t.actual_value, "threshold": t.threshold}
                for t in triggers
            ],
        }

    elif name == "list_alert_triggers":
        from .alerts import AlertManager
        am = AlertManager(ledger)
        triggers = am.list_triggers(
            rule_id=args.get("rule_id"),
            acknowledged=args.get("acknowledged"),
            limit=args.get("limit", 50),
        )
        return [
            {"id": t.id, "rule_name": t.rule_name, "severity": t.severity,
             "message": t.message, "acknowledged": t.acknowledged,
             "triggered_at": t.triggered_at.isoformat()}
            for t in triggers
        ]

    elif name == "acknowledge_alert":
        from .alerts import AlertManager
        am = AlertManager(ledger)
        am.acknowledge_trigger(args["trigger_id"])
        return {"acknowledged": True, "trigger_id": args["trigger_id"]}

    # ── v1.0.0: API Keys ──────────────────────────────────────────

    elif name == "create_api_key":
        from .api_keys import APIKeyManager
        km = APIKeyManager(ledger)
        key, raw_key = km.create_key(
            name=args["name"],
            scopes=args.get("scopes", ["read"]),
            description=args.get("description", ""),
            rate_limit_per_hour=args.get("rate_limit_per_hour"),
        )
        return {
            "id": key.id, "name": key.name, "key_prefix": key.key_prefix,
            "key": raw_key, "scopes": key.scopes,
            "message": "Store this key securely — it won't be shown again.",
        }

    elif name == "list_api_keys":
        from .api_keys import APIKeyManager
        km = APIKeyManager(ledger)
        keys = km.list_keys(active_only=args.get("active_only", False))
        return [
            {"id": k.id, "name": k.name, "key_prefix": k.key_prefix,
             "scopes": k.scopes, "active": k.active,
             "created_at": k.created_at.isoformat(),
             "last_used": k.last_used.isoformat() if k.last_used else None,
             "request_count": k.request_count}
            for k in keys
        ]

    elif name == "revoke_api_key":
        from .api_keys import APIKeyManager
        km = APIKeyManager(ledger)
        km.revoke_key(args["key_id"])
        return {"revoked": True, "key_id": args["key_id"]}

    # ── v1.0.0: Dashboard ─────────────────────────────────────────

    elif name == "generate_dashboard":
        from .dashboard import generate_dashboard_html
        html_content = generate_dashboard_html(
            ledger,
            title=args.get("title", "Agent Ledger Dashboard"),
            include_alerts=args.get("include_alerts", True),
        )
        return {
            "format": "html",
            "size_bytes": len(html_content),
            "html": html_content[:500] + "..." if len(html_content) > 500 else html_content,
            "message": "Full HTML generated. Use save_dashboard_html() to write to file, or GET /dashboard?format=html via REST API.",
        }

    # ── v0.3.0+ Missing Tool Handlers ──────────────────────────────

    elif name == "reverse_entry":
        from_date = args.get("from_date")
        to_date = args.get("to_date")
        reversal = ledger.reverse_entry(
            args["entry_id"],
            reason=args.get("reason"),
        )
        return {
            "status": "reversed",
            "original_entry_id": args["entry_id"],
            "reversal_entry_id": reversal.id,
            "description": reversal.description,
        }

    elif name == "cash_flow_statement":
        from .cashflow import generate_cash_flow_statement, format_cash_flow_statement
        from datetime import datetime
        from_date = None
        to_date = None
        if args.get("from_date"):
            from_date = datetime.fromisoformat(args["from_date"])
        if args.get("to_date"):
            to_date = datetime.fromisoformat(args["to_date"])
        report = generate_cash_flow_statement(
            ledger, from_date=from_date, to_date=to_date
        )
        text = format_cash_flow_statement(report)
        return {"text": text}

    elif name == "apply_template":
        from .templates import apply_template, TEMPLATES
        created = apply_template(ledger, args["template"])
        return {
            "status": "applied",
            "template": args["template"],
            "accounts_created": len(created),
            "accounts": [
                {"code": a.code, "name": a.name, "type": a.account_type.value}
                for a in created
            ],
        }

    elif name == "list_templates":
        from .templates import TEMPLATES
        return [
            {
                "key": key,
                "name": name,
                "account_count": len(accounts),
            }
            for key, (name, accounts) in TEMPLATES.items()
        ]

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
