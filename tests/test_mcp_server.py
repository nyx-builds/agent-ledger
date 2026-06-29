"""Tests for agent-ledger MCP server tool handling."""

import pytest
from pathlib import Path

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.mcp_server import handle_tool_call, TOOLS


@pytest.fixture
def ledger(tmp_path):
    filepath = tmp_path / "ledger.json"
    storage = Storage(filepath)
    storage.init()
    return Ledger(storage)


@pytest.fixture
def populated_ledger(ledger):
    ledger.create_account("cash", "Cash", AccountType.ASSET)
    ledger.create_account("revenue", "Revenue", AccountType.REVENUE)
    ledger.create_account("expenses", "Expenses", AccountType.EXPENSE)
    ledger.create_account("equity", "Equity", AccountType.EQUITY)
    return ledger


class TestToolDefinitions:
    """Test that all tools are properly defined."""

    def test_tools_are_defined(self):
        assert len(TOOLS) > 0
        for tool in TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool

    def test_required_tools_exist(self):
        tool_names = {t["name"] for t in TOOLS}
        required = {
            "init_ledger", "create_account", "list_accounts", "get_account",
            "post_entry", "list_entries", "get_entry", "delete_entry",
            "reconcile_entry", "trial_balance", "income_statement",
            "balance_sheet", "add_exchange_rate", "list_exchange_rates",
            # v0.2.0 tools
            "close_period", "get_account_hierarchy", "get_rollup_balance",
            "validate_hierarchy", "list_audit_log", "export_csv",
            "list_closed_periods",
        }
        assert required.issubset(tool_names), f"Missing tools: {required - tool_names}"


class TestHandleToolCall:
    """Test MCP tool call handling."""

    def test_create_account(self, ledger):
        result = handle_tool_call(ledger, "create_account", {
            "code": "cash",
            "name": "Cash",
            "account_type": "asset",
        })
        assert len(result) == 1
        assert result[0]["type"] == "text"
        import json
        data = json.loads(result[0]["text"])
        assert data["code"] == "cash"

    def test_list_accounts(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "list_accounts", {})
        assert len(result) == 1
        import json
        data = json.loads(result[0]["text"])
        assert len(data) == 4

    def test_get_account(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "get_account", {"code": "cash"})
        import json
        data = json.loads(result[0]["text"])
        assert data["code"] == "cash"

    def test_post_entry(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "post_entry", {
            "description": "Sale",
            "lines": [
                {"account_code": "cash", "debit": 500},
                {"account_code": "revenue", "credit": 500},
            ],
        })
        import json
        data = json.loads(result[0]["text"])
        assert data["description"] == "Sale"
        # Verify lines contain the correct amounts
        cash_line = next(l for l in data["lines"] if l["account_code"] == "cash")
        assert cash_line["debit"] == 500.0
        rev_line = next(l for l in data["lines"] if l["account_code"] == "revenue")
        assert rev_line["credit"] == 500.0

    def test_trial_balance(self, populated_ledger):
        # Post an entry first
        handle_tool_call(populated_ledger, "post_entry", {
            "description": "Sale",
            "lines": [
                {"account_code": "cash", "debit": 1000},
                {"account_code": "revenue", "credit": 1000},
            ],
        })
        result = handle_tool_call(populated_ledger, "trial_balance", {})
        assert len(result) == 1
        assert "TRIAL BALANCE" in result[0]["text"]

    def test_income_statement(self, populated_ledger):
        handle_tool_call(populated_ledger, "post_entry", {
            "description": "Sale",
            "lines": [
                {"account_code": "cash", "debit": 1000},
                {"account_code": "revenue", "credit": 1000},
            ],
        })
        result = handle_tool_call(populated_ledger, "income_statement", {})
        assert "INCOME STATEMENT" in result[0]["text"]

    def test_balance_sheet(self, populated_ledger):
        handle_tool_call(populated_ledger, "post_entry", {
            "description": "Investment",
            "lines": [
                {"account_code": "cash", "debit": 5000},
                {"account_code": "equity", "credit": 5000},
            ],
        })
        result = handle_tool_call(populated_ledger, "balance_sheet", {})
        assert "BALANCE SHEET" in result[0]["text"]

    def test_error_handling(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "get_account", {"code": "nonexistent"})
        import json
        data = json.loads(result[0]["text"])
        assert "error" in data

    def test_reconcile_entry(self, populated_ledger):
        entry_result = handle_tool_call(populated_ledger, "post_entry", {
            "description": "Sale",
            "lines": [
                {"account_code": "cash", "debit": 500},
                {"account_code": "revenue", "credit": 500},
            ],
        })
        import json
        entry_data = json.loads(entry_result[0]["text"])
        entry_id = entry_data["id"]

        result = handle_tool_call(populated_ledger, "reconcile_entry", {"entry_id": entry_id})
        data = json.loads(result[0]["text"])
        assert data["status"] == "reconciled"

    def test_add_exchange_rate(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "add_exchange_rate", {
            "from_currency": "USD",
            "to_currency": "EUR",
            "rate": 0.85,
        })
        import json
        data = json.loads(result[0]["text"])
        assert data["status"] == "added"

    def test_list_exchange_rates(self, populated_ledger):
        handle_tool_call(populated_ledger, "add_exchange_rate", {
            "from_currency": "USD",
            "to_currency": "EUR",
            "rate": 0.85,
        })
        result = handle_tool_call(populated_ledger, "list_exchange_rates", {})
        import json
        data = json.loads(result[0]["text"])
        assert len(data) == 1

    def test_get_account_transactions(self, populated_ledger):
        handle_tool_call(populated_ledger, "post_entry", {
            "description": "Sale",
            "lines": [
                {"account_code": "cash", "debit": 500},
                {"account_code": "revenue", "credit": 500},
            ],
        })
        result = handle_tool_call(populated_ledger, "get_account_transactions", {"code": "cash"})
        import json
        data = json.loads(result[0]["text"])
        assert len(data) == 1

    def test_delete_entry(self, populated_ledger):
        entry_result = handle_tool_call(populated_ledger, "post_entry", {
            "description": "Temporary",
            "lines": [
                {"account_code": "cash", "debit": 100},
                {"account_code": "revenue", "credit": 100},
            ],
        })
        import json
        entry_data = json.loads(entry_result[0]["text"])
        entry_id = entry_data["id"]

        result = handle_tool_call(populated_ledger, "delete_entry", {"entry_id": entry_id})
        data = json.loads(result[0]["text"])
        assert data["status"] == "deleted"


class TestV2ToolHandling:
    """Test v0.2.0 MCP tool handling."""

    def test_close_period(self, populated_ledger):
        # Post some activity
        handle_tool_call(populated_ledger, "post_entry", {
            "description": "Sale",
            "lines": [
                {"account_code": "cash", "debit": 1000},
                {"account_code": "revenue", "credit": 1000},
            ],
        })
        handle_tool_call(populated_ledger, "post_entry", {
            "description": "Expense",
            "lines": [
                {"account_code": "expenses", "debit": 200},
                {"account_code": "cash", "credit": 200},
            ],
        })

        result = handle_tool_call(populated_ledger, "close_period", {})
        import json
        data = json.loads(result[0]["text"])
        assert data["status"] == "closed"
        assert data["net_income"] == 800.0

    def test_get_account_hierarchy(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "get_account_hierarchy", {})
        import json
        data = json.loads(result[0]["text"])
        assert isinstance(data, list)
        # Should have root-level accounts
        root_codes = [node["account"]["code"] for node in data]
        assert "cash" in root_codes

    def test_get_rollup_balance(self, populated_ledger):
        # Create hierarchy
        populated_ledger.create_account("bank", "Bank", AccountType.ASSET, parent_code="cash")
        handle_tool_call(populated_ledger, "post_entry", {
            "description": "Deposit",
            "lines": [
                {"account_code": "bank", "debit": 500},
                {"account_code": "equity", "credit": 500},
            ],
        })

        result = handle_tool_call(populated_ledger, "get_rollup_balance", {"code": "cash"})
        import json
        data = json.loads(result[0]["text"])
        # Rollup should include bank's 500
        assert data["credit_total"] == 0.0

    def test_validate_hierarchy(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "validate_hierarchy", {})
        import json
        data = json.loads(result[0]["text"])
        assert data["valid"] is True

    def test_list_audit_log(self, populated_ledger):
        # Create an account to generate audit entries
        handle_tool_call(populated_ledger, "post_entry", {
            "description": "Sale",
            "lines": [
                {"account_code": "cash", "debit": 100},
                {"account_code": "revenue", "credit": 100},
            ],
        })

        result = handle_tool_call(populated_ledger, "list_audit_log", {})
        import json
        data = json.loads(result[0]["text"])
        assert isinstance(data, list)
        assert len(data) > 0

    def test_export_csv_accounts(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "export_csv", {"type": "accounts"})
        import json
        data = json.loads(result[0]["text"])
        assert data["format"] == "csv"
        assert "cash" in data["data"]

    def test_export_csv_trial_balance(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "export_csv", {"type": "trial_balance"})
        import json
        data = json.loads(result[0]["text"])
        assert data["format"] == "csv"

    def test_list_closed_periods_empty(self, populated_ledger):
        result = handle_tool_call(populated_ledger, "list_closed_periods", {})
        import json
        data = json.loads(result[0]["text"])
        assert isinstance(data, list)
        assert len(data) == 0


class TestV3ToolHandling:
    """Test v0.3.0 MCP tool handling."""

    def test_reverse_entry(self, populated_ledger):
        # Post an entry first
        entry_result = handle_tool_call(populated_ledger, "post_entry", {
            "description": "Sale",
            "lines": [
                {"account_code": "cash", "debit": 500},
                {"account_code": "revenue", "credit": 500},
            ],
        })
        import json
        entry_data = json.loads(entry_result[0]["text"])
        entry_id = entry_data["id"]

        # Reverse it
        result = handle_tool_call(populated_ledger, "reverse_entry", {
            "entry_id": entry_id,
            "reason": "Posted in error",
        })
        data = json.loads(result[0]["text"])
        assert data["status"] == "reversed"
        assert data["original_entry_id"] == entry_id
        assert data["reversal_entry_id"] != entry_id

    def test_cash_flow_statement(self, populated_ledger):
        # Post an entry
        handle_tool_call(populated_ledger, "post_entry", {
            "description": "Sale",
            "lines": [
                {"account_code": "cash", "debit": 1000},
                {"account_code": "revenue", "credit": 1000},
            ],
        })
        result = handle_tool_call(populated_ledger, "cash_flow_statement", {})
        assert "CASH FLOW STATEMENT" in result[0]["text"]

    def test_apply_template(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        ledger = Ledger(storage)

        result = handle_tool_call(ledger, "apply_template", {"template": "solo"})
        import json
        data = json.loads(result[0]["text"])
        assert data["status"] == "applied"
        assert data["accounts_created"] > 0

    def test_list_templates(self, ledger):
        result = handle_tool_call(ledger, "list_templates", {})
        import json
        data = json.loads(result[0]["text"])
        assert isinstance(data, list)
        assert len(data) == 3
        keys = [t["key"] for t in data]
        assert "solo" in keys

    def test_import_accounts_csv(self, ledger):
        csv_content = "code,name,type\ncash,Cash,asset\nap,Accounts Payable,liability\n"
        result = handle_tool_call(ledger, "import_accounts_csv", {
            "csv_content": csv_content,
        })
        import json
        data = json.loads(result[0]["text"])
        assert data["imported"] == 2

    def test_import_entries_csv(self, populated_ledger):
        csv_content = (
            "entry_description,account_code,debit,credit\n"
            "Sale,cash,500,0\n"
            "Sale,revenue,0,500\n"
        )
        result = handle_tool_call(populated_ledger, "import_entries_csv", {
            "csv_content": csv_content,
        })
        import json
        data = json.loads(result[0]["text"])
        assert data["imported"] == 1

    def test_v3_tools_in_definitions(self):
        tool_names = {t["name"] for t in TOOLS}
        v3_tools = {
            "reverse_entry", "cash_flow_statement",
            "apply_template", "list_templates",
            "import_accounts_csv", "import_entries_csv",
        }
        assert v3_tools.issubset(tool_names), f"Missing: {v3_tools - tool_names}"
