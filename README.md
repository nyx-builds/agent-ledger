<div align="center">

# Agent Ledger

**Double-entry accounting ledger for autonomous AI agents**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests: 708](https://img.shields.io/badge/tests-708%20passing-brightgreen.svg)](#testing)
[![MCP](https://img.shields.io/badge/MCP-server-7c3aed)](https://modelcontextprotocol.io)
[![Version: 1.0.0](https://img.shields.io/badge/version-1.0.0-blue.svg)](#changelog)

</div>

---

Double-entry accounting ledger CLI + MCP server for autonomous agents.

Create and manage a chart of accounts, post journal entries with full double-entry validation, and generate financial reports — all from the CLI or via an MCP server for agent integration.

## Features

- **Chart of Accounts** — Asset, Liability, Equity, Revenue, Expense account types
- **Double-Entry Validation** — Every journal entry must balance (debits = credits)
- **Journal Entries** — Multi-line entries with descriptions, timestamps, and tags
- **Financial Reports** — Trial Balance, Income Statement, Balance Sheet, Cash Flow Statement
- **Date-Filtered Reports** — Generate reports as of a specific date or for a date range
- **Multi-Currency** — Accounts in different currencies with exchange rates and conversion
- **Reconciliation** — Mark entries as reconciled, track reconciliation status
- **Entry Reversal** — Reverse any posted entry with an opposing entry
- **Period Close** — Close accounting periods, zero out temporary accounts into retained earnings
- **Account Hierarchy** — Parent-child account relationships with rollup balances
- **Chart of Accounts Templates** — Pre-built templates for Solo, Startup, and Freelancer businesses
- **CSV Import** — Import accounts and journal entries from CSV files
- **Audit Log** — Track all ledger operations with timestamps and actor attribution
- **CSV Export** — Export accounts, entries, and reports to CSV files
- **Cost Centers / Projects** — Dimensional accounting for tracking profitability by project, department, or cost center
- **Multi-Period Comparison** — Side-by-side period analysis with variance and percentage change
- **Data Persistence** — JSON-based storage, portable and human-readable
- **CLI** — Full-featured Click CLI with rich output
- **MCP Server** — Model Context Protocol server for autonomous agent integration

## Installation

```bash
pip install agent-ledger
```

## Quick Start

### CLI

```bash
# Initialize a new ledger
agent-ledger init

# Create accounts
agent-ledger account create cash --type asset
agent-ledger account create revenue --type revenue
agent-ledger account create expenses --type expense

# Create sub-accounts (hierarchy)
agent-ledger account create bank --type asset --parent cash
agent-ledger account create petty-cash --type asset --parent cash

# Post a journal entry
agent-ledger entry post "Initial sale" cash:1000 revenue:1000

# Post with multiple lines
agent-ledger entry post "Purchase supplies" \
  expenses:500 \
  cash:500

# Reverse an entry (creates opposing entry)
agent-ledger entry reverse <entry-id> --reason "Posted in error"

# View trial balance
agent-ledger report trial-balance

# View income statement
agent-ledger report income-statement

# View balance sheet
agent-ledger report balance-sheet

# View cash flow statement
agent-ledger report cash-flow

# List accounts
agent-ledger account list

# Show account details with running balance
agent-ledger account show cash

# View account hierarchy with rollup balances
agent-ledger hierarchy tree
agent-ledger hierarchy rollup cash

# Close a period (zero out revenue/expenses into retained earnings)
agent-ledger close-period

# View audit log
agent-ledger audit list
agent-ledger audit list --action account_created --limit 10

# Export to CSV
agent-ledger export accounts --output accounts.csv
agent-ledger export entries --output entries.csv
agent-ledger export trial-balance --output tb.csv
agent-ledger export income-statement --output is.csv

# Apply a chart of accounts template
agent-ledger template list
agent-ledger template apply solo

# Import from CSV
agent-ledger import accounts accounts.csv
agent-ledger import entries entries.csv
```

### MCP Server

```bash
agent-ledger serve
```

This starts an MCP server that exposes all ledger operations as tools for autonomous agents, including:
- **Core**: `init_ledger`, `create_account`, `list_accounts`, `get_account`, `post_entry`, `list_entries`, `get_entry`, `delete_entry`, `reconcile_entry`
- **Reports**: `trial_balance`, `income_statement`, `balance_sheet`, `cash_flow_statement`
- **Multi-Currency**: `add_exchange_rate`, `list_exchange_rates`
- **v0.2.0**: `close_period`, `get_account_hierarchy`, `get_rollup_balance`, `validate_hierarchy`, `list_audit_log`, `export_csv`, `list_closed_periods`
- **v0.3.0**: `reverse_entry`, `apply_template`, `list_templates`, `import_accounts_csv`, `import_entries_csv`
- **v0.9.0**: `create_cost_center`, `list_cost_centers`, `cost_center_report`, `cost_center_summary`, `assign_entry_to_cost_center`, `compare_account_balances`, `compare_income_statements`

### Cost Centers / Projects

Track revenue and expenses by project, department, or cost center for profitability analysis:

```bash
# Create cost centers
agent-ledger cost-center create proj-alpha "Project Alpha" --type project
agent-ledger cost-center create dept-eng "Engineering" --type department

# Assign entries to cost centers (via MCP or programmatically)
# Then view profitability per project:
agent-ledger cost-center report proj-alpha
agent-ledger cost-center summary
agent-ledger cost-center tree
```

### Multi-Period Comparison

Compare financial performance across time periods with variance analysis:

```bash
# Compare account balances across quarters
agent-ledger compare balances \
  -p 2024-01-01,2024-03-31,Q1 \
  -p 2024-04-01,2024-06-30,Q2

# Compare income statements across quarters
agent-ledger compare income \
  -p 2024-01-01,2024-03-31,Q1 \
  -p 2024-04-01,2024-06-30,Q2
```

## Architecture

```
src/agent_ledger/
├── __init__.py        # Package init
├── models.py          # Core data models (Account, JournalEntry, etc.)
├── ledger.py          # Ledger engine (business logic + audit integration)
├── storage.py         # JSON persistence layer
├── reports.py         # Financial report generators
├── currency.py        # Multi-currency support
├── audit.py           # Audit log (entries, actions, filtering)
├── closing.py         # Period close (zero temporary accounts)
├── hierarchy.py       # Account hierarchy & rollup balances
├── export.py          # CSV export for accounts, entries, reports
├── cashflow.py       # Cash flow statement generation (indirect method)
├── templates.py      # Chart of accounts templates (solo, startup, freelancer)
├── import_csv.py     # CSV import for accounts and journal entries
├── cost_centers.py   # Cost centers / dimensional accounting
├── comparison.py     # Multi-period comparison reports
├── cli.py             # Click CLI
├── mcp_server.py      # MCP server for agent integration
└── exceptions.py      # Custom exceptions
```

## Changelog

### v0.9.0
- **Cost Centers / Projects**: Dimensional accounting for tracking revenue and expenses by project, department, or cost center. Full CRUD, entry assignment, per-center financial reports, cross-center summary, and hierarchy tree
- **Multi-Period Comparison**: Side-by-side period comparison of account balances and income statements with variance and percentage change indicators (up/down/stable/new/gone)
- 11 new MCP tools (89 total), new CLI command groups (`cost-center`, `compare`)
- 66 new tests (580 total)

### v0.2.0
- **Period Close**: Close accounting periods, zero out revenue/expense accounts into retained earnings
- **Account Hierarchy**: Parent-child account relationships with recursive rollup balances
- **Audit Log**: Full audit trail of all ledger operations with actor attribution and before/after snapshots
- **CSV Export**: Export accounts, journal entries, trial balance, income statement, balance sheet, and hierarchy to CSV
- 72 new tests (192 total)

### v0.1.0
- Initial release with chart of accounts, double-entry validation, journal entries, financial reports, multi-currency, reconciliation, CLI, and MCP server
- 120 tests

## License

MIT
