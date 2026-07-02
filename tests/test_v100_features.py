"""Tests for v1.0.0 features: Alerts, API Keys, Dashboard."""

import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from agent_ledger.models import AccountType, JournalLine
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.alerts import (
    AlertManager, AlertCondition, AlertSeverity, AlertRule, AlertTrigger,
)
from agent_ledger.api_keys import APIKeyManager, APIKey, VALID_SCOPES
from agent_ledger.dashboard import generate_dashboard_html, save_dashboard_html


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def ledger():
    """Create a fresh ledger with some accounts and entries."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    path.unlink()  # remove so storage.init() can create it fresh
    try:
        storage = Storage(path)
        storage.init(name="Test Ledger", base_currency="USD")
        ledger = Ledger(storage)

        # Create accounts
        ledger.create_account("cash", "Cash", AccountType.ASSET)
        ledger.create_account("inventory", "Inventory", AccountType.ASSET)
        ledger.create_account("loan", "Bank Loan", AccountType.LIABILITY)
        ledger.create_account("equity", "Owner Equity", AccountType.EQUITY)
        ledger.create_account("revenue", "Sales Revenue", AccountType.REVENUE)
        ledger.create_account("expenses", "Operating Expenses", AccountType.EXPENSE)

        # Post entries
        ledger.post_entry("Initial investment", [
            ("cash", 10000, 0),
            ("equity", 0, 10000),
        ])
        ledger.post_entry("Bank loan", [
            ("cash", 5000, 0),
            ("loan", 0, 5000),
        ])
        ledger.post_entry("Sale", [
            ("cash", 3000, 0),
            ("revenue", 0, 3000),
        ])
        ledger.post_entry("Buy inventory", [
            ("inventory", 2000, 0),
            ("cash", 0, 2000),
        ])
        ledger.post_entry("Pay expenses", [
            ("expenses", 1500, 0),
            ("cash", 0, 1500),
        ])

        yield ledger
    finally:
        path.unlink(missing_ok=True)


# ════════════════════════════════════════════════════════════════════
# ALERT TESTS
# ════════════════════════════════════════════════════════════════════


class TestAlertCondition:
    def test_alert_condition_values(self):
        assert AlertCondition.ABOVE.value == "above"
        assert AlertCondition.BELOW.value == "below"
        assert AlertCondition.EQUALS.value == "equals"
        assert AlertCondition.CHANGED.value == "changed"


class TestAlertSeverity:
    def test_alert_severity_values(self):
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.CRITICAL.value == "critical"


class TestAlertManagerCreate:
    def test_create_rule_basic(self, ledger):
        am = AlertManager(ledger)
        rule = am.create_rule(
            name="Cash too low",
            account_code="cash",
            condition=AlertCondition.BELOW,
            threshold=5000,
        )
        assert rule.id
        assert rule.name == "Cash too low"
        assert rule.account_code == "cash"
        assert rule.condition == AlertCondition.BELOW
        assert rule.threshold == 5000
        assert rule.severity == AlertSeverity.WARNING
        assert rule.enabled is True
        assert rule.cooldown_minutes == 60

    def test_create_rule_with_all_params(self, ledger):
        am = AlertManager(ledger)
        rule = am.create_rule(
            name="Critical cash",
            account_code="cash",
            condition=AlertCondition.ABOVE,
            threshold=20000,
            severity=AlertSeverity.CRITICAL,
            description="Emergency threshold",
            cooldown_minutes=30,
        )
        assert rule.severity == AlertSeverity.CRITICAL
        assert rule.description == "Emergency threshold"
        assert rule.cooldown_minutes == 30

    def test_create_rule_with_string_condition(self, ledger):
        am = AlertManager(ledger)
        rule = am.create_rule(
            name="Test",
            account_code="cash",
            condition="above",
            threshold=100,
        )
        assert rule.condition == AlertCondition.ABOVE

    def test_create_rule_invalid_account(self, ledger):
        am = AlertManager(ledger)
        from agent_ledger.exceptions import AccountNotFoundError
        with pytest.raises(AccountNotFoundError):
            am.create_rule("Test", "nonexistent", AlertCondition.ABOVE, 100)

    def test_create_rule_empty_name(self, ledger):
        am = AlertManager(ledger)
        with pytest.raises(ValueError, match="must not be empty"):
            am.create_rule("", "cash", AlertCondition.ABOVE, 100)

    def test_create_rule_negative_cooldown(self, ledger):
        am = AlertManager(ledger)
        with pytest.raises(ValueError, match="cooldown"):
            am.create_rule("Test", "cash", AlertCondition.ABOVE, 100, cooldown_minutes=-1)


class TestAlertManagerList:
    def test_list_rules_empty(self, ledger):
        am = AlertManager(ledger)
        assert am.list_rules() == []

    def test_list_rules_multiple(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Rule 1", "cash", AlertCondition.ABOVE, 100)
        am.create_rule("Rule 2", "inventory", AlertCondition.BELOW, 500)
        rules = am.list_rules()
        assert len(rules) == 2

    def test_list_rules_filter_by_account(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Cash rule", "cash", AlertCondition.ABOVE, 100)
        am.create_rule("Inv rule", "inventory", AlertCondition.BELOW, 500)
        rules = am.list_rules(account_code="cash")
        assert len(rules) == 1
        assert rules[0].account_code == "cash"

    def test_list_rules_enabled_only(self, ledger):
        am = AlertManager(ledger)
        r1 = am.create_rule("Active", "cash", AlertCondition.ABOVE, 100)
        r2 = am.create_rule("Inactive", "cash", AlertCondition.BELOW, 50)
        am.update_rule(r2.id, enabled=False)
        rules = am.list_rules(enabled_only=True)
        assert len(rules) == 1
        assert rules[0].name == "Active"

    def test_list_rules_filter_by_severity(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Warning", "cash", AlertCondition.ABOVE, 100, severity=AlertSeverity.WARNING)
        am.create_rule("Critical", "cash", AlertCondition.ABOVE, 200, severity=AlertSeverity.CRITICAL)
        rules = am.list_rules(severity=AlertSeverity.CRITICAL)
        assert len(rules) == 1
        assert rules[0].severity == AlertSeverity.CRITICAL


class TestAlertManagerUpdate:
    def test_update_rule_threshold(self, ledger):
        am = AlertManager(ledger)
        rule = am.create_rule("Test", "cash", AlertCondition.ABOVE, 100)
        updated = am.update_rule(rule.id, threshold=500)
        assert updated.threshold == 500

    def test_update_rule_enabled(self, ledger):
        am = AlertManager(ledger)
        rule = am.create_rule("Test", "cash", AlertCondition.ABOVE, 100)
        updated = am.update_rule(rule.id, enabled=False)
        assert updated.enabled is False

    def test_update_rule_not_found(self, ledger):
        am = AlertManager(ledger)
        from agent_ledger.exceptions import LedgerError
        with pytest.raises(LedgerError):
            am.update_rule("nonexistent", threshold=500)


class TestAlertManagerDelete:
    def test_delete_rule(self, ledger):
        am = AlertManager(ledger)
        rule = am.create_rule("Test", "cash", AlertCondition.ABOVE, 100)
        am.delete_rule(rule.id)
        assert am.list_rules() == []

    def test_delete_rule_not_found(self, ledger):
        am = AlertManager(ledger)
        from agent_ledger.exceptions import LedgerError
        with pytest.raises(LedgerError):
            am.delete_rule("nonexistent")


class TestAlertManagerCheck:
    def test_check_triggers_above(self, ledger):
        """Cash balance is 14500 (10000 + 5000 + 3000 - 2000 - 1500 = 14500).
        Rule: cash ABOVE 10000 should trigger."""
        am = AlertManager(ledger)
        am.create_rule("Cash high", "cash", AlertCondition.ABOVE, 10000)
        triggers = am.check_rules()
        assert len(triggers) == 1
        assert triggers[0].rule_name == "Cash high"
        assert triggers[0].actual_value == 14500

    def test_check_triggers_below(self, ledger):
        """Cash balance 14500. Rule: cash BELOW 20000 should trigger."""
        am = AlertManager(ledger)
        am.create_rule("Cash low", "cash", AlertCondition.BELOW, 20000)
        triggers = am.check_rules()
        assert len(triggers) == 1

    def test_check_no_trigger(self, ledger):
        """Cash balance 14500. Rule: cash ABOVE 100000 should NOT trigger."""
        am = AlertManager(ledger)
        am.create_rule("Cash very high", "cash", AlertCondition.ABOVE, 100000)
        triggers = am.check_rules()
        assert len(triggers) == 0

    def test_check_triggers_equals(self, ledger):
        """Post an entry to make cash exactly 15000."""
        ledger.post_entry("Adjustment", [("cash", 500, 0), ("revenue", 0, 500)])
        am = AlertManager(ledger)
        am.create_rule("Exact", "cash", AlertCondition.EQUALS, 15000)
        triggers = am.check_rules()
        assert len(triggers) == 1

    def test_check_disabled_rule_not_triggered(self, ledger):
        am = AlertManager(ledger)
        rule = am.create_rule("Disabled", "cash", AlertCondition.ABOVE, 100)
        am.update_rule(rule.id, enabled=False)
        triggers = am.check_rules()
        assert len(triggers) == 0

    def test_check_cooldown_prevents_retrigger(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Cooldown test", "cash", AlertCondition.ABOVE, 100,
                       cooldown_minutes=60)
        # First check
        triggers1 = am.check_rules()
        assert len(triggers1) == 1
        # Second check immediately - should be in cooldown
        triggers2 = am.check_rules()
        assert len(triggers2) == 0

    def test_trigger_has_correct_fields(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Test rule", "cash", AlertCondition.ABOVE, 100,
                       severity=AlertSeverity.CRITICAL)
        triggers = am.check_rules()
        assert len(triggers) == 1
        t = triggers[0]
        assert t.id
        assert t.rule_name == "Test rule"
        assert t.account_code == "cash"
        assert t.condition == "above"
        assert t.threshold == 100
        assert t.severity == "critical"
        assert t.acknowledged is False
        assert "above threshold" in t.message

    def test_check_changed_condition_first_run(self, ledger):
        """CHANGED condition should always fire on first check."""
        am = AlertManager(ledger)
        am.create_rule("Changed", "cash", AlertCondition.CHANGED, 0)
        triggers = am.check_rules()
        assert len(triggers) == 1


class TestAlertManagerTriggers:
    def test_list_triggers(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Test", "cash", AlertCondition.ABOVE, 100)
        am.check_rules()
        triggers = am.list_triggers()
        assert len(triggers) == 1

    def test_acknowledge_trigger(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Test", "cash", AlertCondition.ABOVE, 100)
        triggers = am.check_rules()
        trigger_id = triggers[0].id
        acked = am.acknowledge_trigger(trigger_id)
        assert acked.acknowledged is True

    def test_acknowledge_all(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Rule 1", "cash", AlertCondition.ABOVE, 100)
        am.create_rule("Rule 2", "inventory", AlertCondition.ABOVE, 100)
        am.check_rules()
        count = am.acknowledge_all()
        assert count == 2

    def test_clear_triggers(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Test", "cash", AlertCondition.ABOVE, 100)
        am.check_rules()
        am.acknowledge_all()
        cleared = am.clear_triggers(acknowledged_only=True)
        assert cleared == 1
        assert am.list_triggers() == []

    def test_clear_all_triggers(self, ledger):
        am = AlertManager(ledger)
        am.create_rule("Test", "cash", AlertCondition.ABOVE, 100)
        am.check_rules()
        cleared = am.clear_triggers(acknowledged_only=False)
        assert cleared == 1

    def test_list_triggers_filter_by_rule(self, ledger):
        am = AlertManager(ledger)
        r1 = am.create_rule("Rule 1", "cash", AlertCondition.ABOVE, 100)
        am.create_rule("Rule 2", "inventory", AlertCondition.ABOVE, 100)
        am.check_rules()
        triggers = am.list_triggers(rule_id=r1.id)
        assert len(triggers) == 1
        assert triggers[0].rule_id == r1.id


# ════════════════════════════════════════════════════════════════════
# API KEY TESTS
# ════════════════════════════════════════════════════════════════════


class TestAPIKeyGeneration:
    def test_generate_key_format(self):
        key = APIKeyManager.generate_key()
        assert key.startswith("agl_")
        assert len(key) == 4 + 32  # prefix + 32 hex chars

    def test_generate_key_unique(self):
        key1 = APIKeyManager.generate_key()
        key2 = APIKeyManager.generate_key()
        assert key1 != key2


class TestAPIKeyManagerCreate:
    def test_create_key_basic(self, ledger):
        km = APIKeyManager(ledger)
        key, raw_key = km.create_key(name="Test Key")
        assert key.name == "Test Key"
        assert key.id
        assert raw_key.startswith("agl_")
        assert "read" in key.scopes

    def test_create_key_with_scopes(self, ledger):
        km = APIKeyManager(ledger)
        key, raw_key = km.create_key(
            name="Admin Key",
            scopes=["read", "write", "admin"],
        )
        assert "admin" in key.scopes
        assert "write" in key.scopes

    def test_create_key_invalid_scope(self, ledger):
        km = APIKeyManager(ledger)
        with pytest.raises(ValueError, match="Invalid scope"):
            km.create_key(name="Bad", scopes=["invalid_scope"])

    def test_create_key_empty_name(self, ledger):
        km = APIKeyManager(ledger)
        with pytest.raises(ValueError, match="must not be empty"):
            km.create_key(name="")

    def test_create_key_with_rate_limit(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Limited", rate_limit_per_hour=100)
        assert key.rate_limit_per_hour == 100

    def test_create_key_with_description(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Test", description="For testing")
        assert key.description == "For testing"


class TestAPIKeyManagerValidate:
    def test_validate_key_valid(self, ledger):
        km = APIKeyManager(ledger)
        key, raw_key = km.create_key(name="Test")
        validated = km.validate_key(raw_key)
        assert validated is not None
        assert validated.id == key.id

    def test_validate_key_invalid_format(self, ledger):
        km = APIKeyManager(ledger)
        assert km.validate_key("not_a_key") is None

    def test_validate_key_wrong_prefix(self, ledger):
        km = APIKeyManager(ledger)
        assert km.validate_key("bad_abc123") is None

    def test_validate_key_nonexistent(self, ledger):
        km = APIKeyManager(ledger)
        fake_key = "agl_" + "a" * 32
        assert km.validate_key(fake_key) is None

    def test_validate_revoked_key(self, ledger):
        km = APIKeyManager(ledger)
        key, raw_key = km.create_key(name="Test")
        km.revoke_key(key.id)
        assert km.validate_key(raw_key) is None

    def test_validate_updates_usage_stats(self, ledger):
        km = APIKeyManager(ledger)
        key, raw_key = km.create_key(name="Test")
        km.validate_key(raw_key)
        keys = km.list_keys()
        assert keys[0].request_count == 1
        assert keys[0].last_used is not None

    def test_validate_key_has_scope(self, ledger):
        km = APIKeyManager(ledger)
        key, raw_key = km.create_key(name="Test", scopes=["read", "reports"])
        validated = km.validate_key(raw_key)
        assert validated.has_scope("read")
        assert validated.has_scope("reports")
        assert not validated.has_scope("write")

    def test_admin_scope_implies_all(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Admin", scopes=["admin"])
        assert key.has_scope("read")
        assert key.has_scope("write")
        assert key.has_scope("reconcile")


class TestAPIKeyManagerList:
    def test_list_keys_empty(self, ledger):
        km = APIKeyManager(ledger)
        assert km.list_keys() == []

    def test_list_keys(self, ledger):
        km = APIKeyManager(ledger)
        km.create_key(name="Key 1")
        km.create_key(name="Key 2")
        keys = km.list_keys()
        assert len(keys) == 2

    def test_list_keys_active_only(self, ledger):
        km = APIKeyManager(ledger)
        k1, _ = km.create_key(name="Active")
        k2, _ = km.create_key(name="Inactive")
        km.revoke_key(k2.id)
        keys = km.list_keys(active_only=True)
        assert len(keys) == 1
        assert keys[0].name == "Active"

    def test_list_keys_raw_key_not_exposed(self, ledger):
        km = APIKeyManager(ledger)
        km.create_key(name="Test")
        keys = km.list_keys()
        # key_prefix is visible but key_hash should not be reversible
        assert keys[0].key_prefix
        assert keys[0].key_hash
        # The raw key is not stored anywhere in the key object
        assert not hasattr(keys[0], 'raw_key')


class TestAPIKeyManagerRevoke:
    def test_revoke_key(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Test")
        revoked = km.revoke_key(key.id)
        assert revoked.active is False

    def test_revoke_key_not_found(self, ledger):
        km = APIKeyManager(ledger)
        with pytest.raises(KeyError):
            km.revoke_key("nonexistent")


class TestAPIKeyManagerDelete:
    def test_delete_key(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Test")
        km.delete_key(key.id)
        assert km.list_keys() == []

    def test_delete_key_not_found(self, ledger):
        km = APIKeyManager(ledger)
        with pytest.raises(KeyError):
            km.delete_key("nonexistent")


class TestAPIKeyManagerUpdate:
    def test_update_key_scopes(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Test")
        updated = km.update_key(key.id, scopes=["read", "write"])
        assert "write" in updated.scopes

    def test_update_key_description(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Test")
        updated = km.update_key(key.id, description="New description")
        assert updated.description == "New description"

    def test_update_key_invalid_scope(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Test")
        with pytest.raises(ValueError):
            km.update_key(key.id, scopes=["invalid"])


class TestAPIKeyManagerRateLimit:
    def test_check_rate_limit_unlimited(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Test")
        allowed, remaining = km.check_rate_limit(key.id)
        assert allowed is True
        assert remaining == -1  # unlimited

    def test_check_rate_limit_with_limit(self, ledger):
        km = APIKeyManager(ledger)
        key, _ = km.create_key(name="Test", rate_limit_per_hour=100)
        # Make some requests
        for _ in range(10):
            km.validate_key("agl_" + "x" * 32)  # won't match, no effect
        allowed, remaining = km.check_rate_limit(key.id)
        assert allowed is True
        assert remaining == 100  # No actual requests yet


class TestAPIKeyExpiry:
    def test_key_is_valid_not_expired(self, ledger):
        km = APIKeyManager(ledger)
        future = datetime.now(timezone.utc) + timedelta(days=30)
        key, _ = km.create_key(name="Test", expires_at=future)
        assert key.is_valid is True

    def test_key_is_valid_expired(self, ledger):
        km = APIKeyManager(ledger)
        past = datetime.now(timezone.utc) - timedelta(days=1)
        key, _ = km.create_key(name="Test", expires_at=past)
        assert key.is_valid is False


class TestAPIKeyStandalone:
    def test_standalone_key_manager(self):
        """APIKeyManager works without a ledger."""
        km = APIKeyManager()
        key, raw_key = km.create_key(name="Standalone")
        assert key.name == "Standalone"
        validated = km.validate_key(raw_key)
        assert validated is not None


# ════════════════════════════════════════════════════════════════════
# DASHBOARD TESTS
# ════════════════════════════════════════════════════════════════════


class TestDashboardHTML:
    def test_generate_dashboard_html_basic(self, ledger):
        html = generate_dashboard_html(ledger)
        assert "<html" in html
        assert "</html>" in html
        assert "Agent Ledger Dashboard" in html

    def test_generate_dashboard_contains_balance_sheet(self, ledger):
        html = generate_dashboard_html(ledger)
        assert "Balance Sheet" in html
        assert "Total Assets" in html
        assert "Total Liabilities" in html

    def test_generate_dashboard_contains_income_statement(self, ledger):
        html = generate_dashboard_html(ledger)
        assert "Income Statement" in html
        assert "Total Revenue" in html
        assert "Net Income" in html

    def test_generate_dashboard_contains_trial_balance(self, ledger):
        html = generate_dashboard_html(ledger)
        assert "Trial Balance" in html
        assert "Balanced" in html

    def test_generate_dashboard_contains_ratios(self, ledger):
        html = generate_dashboard_html(ledger)
        assert "Financial Ratios" in html
        assert "Current Ratio" in html
        assert "Debt-to-Equity" in html

    def test_generate_dashboard_with_custom_title(self, ledger):
        html = generate_dashboard_html(ledger, title="My Custom Dashboard")
        assert "My Custom Dashboard" in html

    def test_generate_dashboard_with_alerts(self, ledger):
        # Create an alert and trigger it
        am = AlertManager(ledger)
        am.create_rule("Cash alert", "cash", AlertCondition.ABOVE, 100)
        am.check_rules()
        html = generate_dashboard_html(ledger, include_alerts=True)
        assert "Active Alerts" in html
        assert "Cash alert" in html or "above threshold" in html

    def test_generate_dashboard_without_alerts(self, ledger):
        html = generate_dashboard_html(ledger, include_alerts=False)
        # Should not have alerts section header (even if there are triggers)
        assert "Active Alerts" not in html

    def test_generate_dashboard_contains_account_names(self, ledger):
        html = generate_dashboard_html(ledger)
        assert "Cash" in html
        assert "Sales Revenue" in html

    def test_generate_dashboard_html_escapes_special_chars(self, ledger):
        ledger.data.name = "Test <script>alert(1)</script>"
        html = generate_dashboard_html(ledger)
        assert "<script>" not in html  # Should be escaped

    def test_generate_dashboard_has_css(self, ledger):
        html = generate_dashboard_html(ledger)
        assert "<style>" in html
        assert "background" in html.lower()

    def test_generate_dashboard_health_badge(self, ledger):
        html = generate_dashboard_html(ledger)
        assert "health-badge" in html


class TestDashboardSaveFile:
    def test_save_dashboard_html(self, ledger, tmp_path):
        output = tmp_path / "test_dashboard.html"
        save_dashboard_html(ledger, str(output))
        assert output.exists()
        content = output.read_text()
        assert "<html" in content

    def test_save_dashboard_html_custom_title(self, ledger, tmp_path):
        output = tmp_path / "custom.html"
        save_dashboard_html(ledger, str(output), title="Custom Title")
        content = output.read_text()
        assert "Custom Title" in content


# ════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS
# ════════════════════════════════════════════════════════════════════


class TestAlertsAPIKeysIntegration:
    def test_alerts_and_keys_coexist(self, ledger):
        """Alerts and API keys both use metadata — ensure they don't conflict."""
        am = AlertManager(ledger)
        km = APIKeyManager(ledger)

        am.create_rule("Alert 1", "cash", AlertCondition.ABOVE, 100)
        km.create_key(name="Key 1")

        # Both should work independently
        assert len(am.list_rules()) == 1
        assert len(km.list_keys()) == 1
        assert "alert_rules" in ledger.data.metadata
        assert "api_keys" in ledger.data.metadata

    def test_dashboard_with_alerts_and_entries(self, ledger):
        """Dashboard should render correctly with alerts triggered."""
        am = AlertManager(ledger)
        am.create_rule("High cash", "cash", AlertCondition.ABOVE, 100, severity=AlertSeverity.CRITICAL)
        am.create_rule("Low cash", "cash", AlertCondition.BELOW, 100000, severity=AlertSeverity.WARNING)
        am.check_rules()

        html = generate_dashboard_html(ledger)
        assert "Active Alerts" in html
        assert len(am.list_triggers()) == 2

    def test_full_workflow(self, ledger):
        """Full workflow: create key, create alerts, check alerts, generate dashboard."""
        # Create API key
        km = APIKeyManager(ledger)
        key, raw_key = km.create_key(name="Workflow", scopes=["read", "admin"])

        # Validate key works
        validated = km.validate_key(raw_key)
        assert validated is not None

        # Create alerts
        am = AlertManager(ledger)
        am.create_rule("Cash monitor", "cash", AlertCondition.ABOVE, 100)
        triggers = am.check_rules()
        assert len(triggers) >= 1

        # Generate dashboard
        html = generate_dashboard_html(ledger)
        assert "Agent Ledger" in html
