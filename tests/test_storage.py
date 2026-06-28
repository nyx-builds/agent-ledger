"""Tests for agent-ledger storage layer."""

import json
import pytest
from pathlib import Path

from agent_ledger.models import Account, AccountType, LedgerData
from agent_ledger.storage import Storage
from agent_ledger.exceptions import LedgerNotInitializedError


class TestStorage:
    """Test Storage class."""

    def test_init_creates_file(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        data = storage.init(name="Test Ledger", base_currency="EUR")
        assert filepath.exists()
        assert data.name == "Test Ledger"
        assert data.base_currency == "EUR"

    def test_init_already_exists_raises(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        with pytest.raises(FileExistsError):
            storage.init()

    def test_load_existing(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init(name="My Ledger")
        data = storage.load()
        assert data.name == "My Ledger"

    def test_load_nonexistent_raises(self, tmp_path):
        filepath = tmp_path / "nonexistent.json"
        storage = Storage(filepath)
        with pytest.raises(LedgerNotInitializedError):
            storage.load()

    def test_save_and_load_roundtrip(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        data = storage.init(name="Round Trip")

        # Add an account
        data.accounts["cash"] = Account(
            code="cash", name="Cash", account_type=AccountType.ASSET
        )
        storage.save(data)

        # Load and verify
        loaded = storage.load()
        assert "cash" in loaded.accounts
        assert loaded.accounts["cash"].name == "Cash"

    def test_exists(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        assert not storage.exists()
        storage.init()
        assert storage.exists()

    def test_delete(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init()
        assert filepath.exists()
        storage.delete()
        assert not filepath.exists()

    def test_json_is_valid(self, tmp_path):
        filepath = tmp_path / "ledger.json"
        storage = Storage(filepath)
        storage.init(name="JSON Test")
        # Verify it's valid JSON
        raw = json.loads(filepath.read_text())
        assert raw["name"] == "JSON Test"
