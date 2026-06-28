# Agent Ledger

Double-entry accounting ledger CLI + MCP server for autonomous agents.

Create and manage a chart of accounts, post journal entries with full double-entry validation, and generate financial reports — all from the CLI or via an MCP server for agent integration.

## Features

- **Chart of Accounts** — Asset, Liability, Equity, Revenue, Expense account types
- **Double-Entry Validation** — Every journal entry must balance (debits = credits)
- **Journal Entries** — Multi-line entries with descriptions, timestamps, and tags
- **Financial Reports** — Trial Balance, Income Statement, Balance Sheet
- **Multi-Currency** — Accounts in different currencies with exchange rates
- **Reconciliation** — Mark entries as reconciled, track reconciliation status
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

# Post a journal entry
agent-ledger entry post "Initial sale" cash:1000 revenue:1000

# Post with multiple lines
agent-ledger entry post "Purchase supplies" \
  expenses:500 \
  cash:500

# View trial balance
agent-ledger report trial-balance

# View income statement
agent-ledger report income-statement

# View balance sheet
agent-ledger report balance-sheet

# List accounts
agent-ledger account list

# Show account details with running balance
agent-ledger account show cash
```

### MCP Server

```bash
agent-ledger serve
```

This starts an MCP server that exposes all ledger operations as tools for autonomous agents.

## Architecture

```
src/agent_ledger/
├── __init__.py        # Package init
├── models.py          # Core data models (Account, JournalEntry, etc.)
├── ledger.py          # Ledger engine (business logic)
├── storage.py         # JSON persistence layer
├── reports.py         # Financial report generators
├── currency.py        # Multi-currency support
├── cli.py             # Click CLI
├── mcp_server.py      # MCP server for agent integration
└── exceptions.py      # Custom exceptions
```

## License

MIT
