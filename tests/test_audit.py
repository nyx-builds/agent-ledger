"""Tests for audit log functionality."""

import pytest
from datetime import datetime, timezone, timedelta

from agent_ledger.audit import AuditLog, AuditEntry, AuditAction


class TestAuditEntry:
    def test_create_audit_entry(self):
        entry = AuditEntry(
            action=AuditAction.ACCOUNT_CREATE,
            details={"code": "cash", "name": "Cash"},
        )
        assert entry.action == AuditAction.ACCOUNT_CREATE
        assert entry.details == {"code": "cash", "name": "Cash"}
        assert entry.actor == "system"
        assert entry.id is not None
        assert entry.timestamp is not None

    def test_audit_entry_with_all_fields(self):
        now = datetime.now(timezone.utc)
        entry = AuditEntry(
            action=AuditAction.ENTRY_POST,
            actor="agent-1",
            details={"entry_id": "abc"},
            before={"status": "empty"},
            after={"status": "posted"},
            timestamp=now,
        )
        assert entry.actor == "agent-1"
        assert entry.before == {"status": "empty"}
        assert entry.after == {"status": "posted"}
        assert entry.timestamp == now


class TestAuditAction:
    def test_all_actions_defined(self):
        expected = [
            "ledger_init", "account_create", "account_update", "account_delete",
            "entry_post", "entry_delete", "entry_reconcile", "entry_unreconcile",
            "period_close", "exchange_rate_add", "export",
        ]
        actual = [a.value for a in AuditAction]
        for e in expected:
            assert e in actual, f"Missing action: {e}"

    def test_action_is_string_enum(self):
        assert AuditAction.ACCOUNT_CREATE == "account_create"
        assert isinstance(AuditAction.ACCOUNT_CREATE, str)


class TestAuditLog:
    def test_log_entry(self):
        log = AuditLog()
        entry = log.log(AuditAction.ACCOUNT_CREATE, details={"code": "cash"})
        assert log.count == 1
        assert entry.action == AuditAction.ACCOUNT_CREATE

    def test_log_multiple_entries(self):
        log = AuditLog()
        log.log(AuditAction.ACCOUNT_CREATE)
        log.log(AuditAction.ENTRY_POST)
        log.log(AuditAction.ENTRY_DELETE)
        assert log.count == 3

    def test_list_entries_all(self):
        log = AuditLog()
        log.log(AuditAction.ACCOUNT_CREATE)
        log.log(AuditAction.ENTRY_POST)
        entries = log.list_entries()
        assert len(entries) == 2

    def test_list_entries_filtered_by_action(self):
        log = AuditLog()
        log.log(AuditAction.ACCOUNT_CREATE)
        log.log(AuditAction.ENTRY_POST)
        log.log(AuditAction.ACCOUNT_DELETE)
        entries = log.list_entries(action=AuditAction.ACCOUNT_CREATE)
        assert len(entries) == 1
        assert entries[0].action == AuditAction.ACCOUNT_CREATE

    def test_list_entries_filtered_by_actor(self):
        log = AuditLog()
        log.log(AuditAction.ACCOUNT_CREATE, actor="agent-1")
        log.log(AuditAction.ACCOUNT_CREATE, actor="agent-2")
        log.log(AuditAction.ENTRY_POST, actor="agent-1")
        entries = log.list_entries(actor="agent-1")
        assert len(entries) == 2

    def test_list_entries_filtered_by_date(self):
        log = AuditLog()
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=2)

        # Create entry with explicit timestamp
        entry1 = AuditEntry(action=AuditAction.ACCOUNT_CREATE, timestamp=past)
        log._entries.append(entry1)
        log.log(AuditAction.ENTRY_POST)  # current time

        entries = log.list_entries(start_date=now - timedelta(minutes=30))
        assert len(entries) == 1
        assert entries[0].action == AuditAction.ENTRY_POST

    def test_list_entries_limit(self):
        log = AuditLog()
        for i in range(10):
            log.log(AuditAction.ACCOUNT_CREATE)
        entries = log.list_entries(limit=5)
        assert len(entries) == 5

    def test_list_entries_sorted_newest_first(self):
        log = AuditLog()
        log.log(AuditAction.ACCOUNT_CREATE)
        log.log(AuditAction.ENTRY_POST)
        entries = log.list_entries()
        # Most recent first
        assert entries[0].action == AuditAction.ENTRY_POST
        assert entries[1].action == AuditAction.ACCOUNT_CREATE

    def test_get_entry_by_id(self):
        log = AuditLog()
        entry = log.log(AuditAction.ACCOUNT_CREATE)
        found = log.get_entry(entry.id)
        assert found.id == entry.id

    def test_get_entry_not_found(self):
        log = AuditLog()
        with pytest.raises(ValueError):
            log.get_entry("nonexistent-id")

    def test_clear(self):
        log = AuditLog()
        log.log(AuditAction.ACCOUNT_CREATE)
        log.log(AuditAction.ENTRY_POST)
        count = log.clear()
        assert count == 2
        assert log.count == 0

    def test_to_dict_list_and_from_dict_list(self):
        log = AuditLog()
        log.log(AuditAction.ACCOUNT_CREATE, details={"code": "cash"})
        log.log(AuditAction.ENTRY_POST, details={"amount": 100})

        # Serialize
        data = log.to_dict_list()
        assert len(data) == 2

        # Deserialize
        log2 = AuditLog()
        log2.from_dict_list(data)
        assert log2.count == 2
        entries = log2.list_entries()
        assert entries[-1].action == AuditAction.ACCOUNT_CREATE  # oldest last in list

    def test_with_before_after(self):
        log = AuditLog()
        entry = log.log(
            AuditAction.ACCOUNT_UPDATE,
            before={"name": "Old Name"},
            after={"name": "New Name"},
        )
        assert entry.before == {"name": "Old Name"}
        assert entry.after == {"name": "New Name"}
