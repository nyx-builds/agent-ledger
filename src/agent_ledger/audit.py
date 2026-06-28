"""Audit log for agent-ledger — tracks all changes to the ledger."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AuditAction(str, Enum):
    """Types of actions that can be audited."""
    LEDGER_INIT = "ledger_init"
    ACCOUNT_CREATE = "account_create"
    ACCOUNT_UPDATE = "account_update"
    ACCOUNT_DELETE = "account_delete"
    ENTRY_POST = "entry_post"
    ENTRY_DELETE = "entry_delete"
    ENTRY_RECONCILE = "entry_reconcile"
    ENTRY_UNRECONCILE = "entry_unreconcile"
    PERIOD_CLOSE = "period_close"
    EXCHANGE_RATE_ADD = "exchange_rate_add"
    EXPORT = "export"


class AuditEntry(BaseModel):
    """A single audit log entry."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: AuditAction
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str = Field(default="system", description="Who or what performed the action")
    details: dict = Field(default_factory=dict, description="Action-specific details")
    before: Optional[dict] = Field(default=None, description="State before the action")
    after: Optional[dict] = Field(default=None, description="State after the action")


class AuditLog:
    """In-memory audit log that can be persisted alongside ledger data."""

    def __init__(self):
        self._entries: list[AuditEntry] = []

    def log(
        self,
        action: AuditAction,
        actor: str = "system",
        details: Optional[dict] = None,
        before: Optional[dict] = None,
        after: Optional[dict] = None,
    ) -> AuditEntry:
        """Record an audit entry."""
        entry = AuditEntry(
            action=action,
            actor=actor,
            details=details or {},
            before=before,
            after=after,
        )
        self._entries.append(entry)
        return entry

    def list_entries(
        self,
        action: Optional[AuditAction] = None,
        actor: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """List audit entries with optional filters."""
        entries = list(self._entries)

        if action is not None:
            entries = [e for e in entries if e.action == action]
        if actor is not None:
            entries = [e for e in entries if e.actor == actor]
        if start_date is not None:
            entries = [e for e in entries if e.timestamp >= start_date]
        if end_date is not None:
            entries = [e for e in entries if e.timestamp <= end_date]

        # Most recent first
        entries = sorted(entries, key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]

    def get_entry(self, entry_id: str) -> AuditEntry:
        """Get a specific audit entry by ID."""
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        raise ValueError(f"Audit entry '{entry_id}' not found")

    def clear(self) -> int:
        """Clear all audit entries. Returns the count of cleared entries."""
        count = len(self._entries)
        self._entries.clear()
        return count

    @property
    def count(self) -> int:
        """Number of audit entries."""
        return len(self._entries)

    def to_dict_list(self) -> list[dict]:
        """Serialize all entries to a list of dicts."""
        return [json.loads(e.model_dump_json()) for e in self._entries]

    def from_dict_list(self, data: list[dict]) -> None:
        """Load entries from a list of dicts."""
        import json
        self._entries = [AuditEntry.model_validate(d) for d in data]


# Need json import for to_dict_list
import json
