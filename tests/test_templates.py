"""Tests for chart of accounts templates."""

import pytest

from agent_ledger.models import AccountType
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.templates import (
    get_template_names, apply_template, TEMPLATES,
    SOLO_BUSINESS, STARTUP_TEMPLATE, FREELANCER_TEMPLATE,
)


@pytest.fixture
def empty_ledger(tmp_path):
    """Create an empty ledger."""
    filepath = tmp_path / "ledger.json"
    storage = Storage(filepath)
    storage.init(name="Template Test")
    return Ledger(storage)


class TestTemplateDefinitions:
    """Test template data definitions."""

    def test_solo_template_has_accounts(self):
        assert len(SOLO_BUSINESS) > 10

    def test_startup_template_has_accounts(self):
        assert len(STARTUP_TEMPLATE) > 10

    def test_freelancer_template_has_accounts(self):
        assert len(FREELANCER_TEMPLATE) > 10

    def test_all_templates_in_dict(self):
        assert "solo" in TEMPLATES
        assert "startup" in TEMPLATES
        assert "freelancer" in TEMPLATES

    def test_solo_has_standard_account_types(self):
        types = set(t for _, _, t, _, _ in SOLO_BUSINESS)
        assert AccountType.ASSET in types
        assert AccountType.LIABILITY in types
        assert AccountType.EQUITY in types
        assert AccountType.REVENUE in types
        assert AccountType.EXPENSE in types

    def test_startup_has_standard_account_types(self):
        types = set(t for _, _, t, _, _ in STARTUP_TEMPLATE)
        assert AccountType.ASSET in types
        assert AccountType.LIABILITY in types
        assert AccountType.EQUITY in types
        assert AccountType.REVENUE in types
        assert AccountType.EXPENSE in types


class TestGetTemplateNames:
    """Test template listing."""

    def test_returns_all_templates(self):
        names = get_template_names()
        assert len(names) == 3
        keys = [n["key"] for n in names]
        assert "solo" in keys
        assert "startup" in keys
        assert "freelancer" in keys

    def test_includes_account_count(self):
        names = get_template_names()
        for n in names:
            assert n["account_count"] > 0

    def test_includes_name(self):
        names = get_template_names()
        for n in names:
            assert n["name"]


class TestApplyTemplate:
    """Test template application."""

    def test_apply_solo(self, empty_ledger):
        created = apply_template(empty_ledger, "solo")
        assert len(created) == len(SOLO_BUSINESS)

    def test_apply_startup(self, empty_ledger):
        created = apply_template(empty_ledger, "startup")
        assert len(created) == len(STARTUP_TEMPLATE)

    def test_apply_freelancer(self, empty_ledger):
        created = apply_template(empty_ledger, "freelancer")
        assert len(created) == len(FREELANCER_TEMPLATE)

    def test_accounts_actually_created(self, empty_ledger):
        apply_template(empty_ledger, "solo")
        accounts = empty_ledger.list_accounts()
        assert len(accounts) == len(SOLO_BUSINESS)

    def test_cash_account_exists_after_template(self, empty_ledger):
        apply_template(empty_ledger, "solo")
        cash = empty_ledger.get_account("1000")
        assert cash.name == "Cash"
        assert cash.account_type == AccountType.ASSET

    def test_invalid_template_raises(self, empty_ledger):
        with pytest.raises(ValueError, match="Unknown template"):
            apply_template(empty_ledger, "nonexistent")

    def test_apply_template_with_existing_accounts(self, empty_ledger):
        empty_ledger.create_account("1000", "Existing Cash", AccountType.ASSET)
        created = apply_template(empty_ledger, "solo")
        # Should skip the existing account
        assert len(created) == len(SOLO_BUSINESS) - 1

    def test_template_accounts_have_currencies(self, empty_ledger):
        created = apply_template(empty_ledger, "solo")
        for account in created:
            assert account.currency == "USD"
