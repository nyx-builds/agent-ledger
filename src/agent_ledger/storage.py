"""JSON-based persistence layer for agent-ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import LedgerData


class Storage:
    """Handles reading and writing ledger data to JSON files."""

    def __init__(self, filepath: Optional[Path] = None):
        self.filepath = filepath or Path("ledger.json")

    def exists(self) -> bool:
        """Check if the ledger file exists."""
        return self.filepath.exists()

    def load(self) -> LedgerData:
        """Load ledger data from JSON file."""
        if not self.filepath.exists():
            from .exceptions import LedgerNotInitializedError
            raise LedgerNotInitializedError(
                f"Ledger file not found at {self.filepath}. Run 'agent-ledger init' first."
            )

        raw = json.loads(self.filepath.read_text(encoding="utf-8"))
        return LedgerData.model_validate(raw)

    def save(self, data: LedgerData) -> None:
        """Save ledger data to JSON file."""
        from datetime import datetime, timezone
        data.updated_at = datetime.now(timezone.utc)

        # Ensure directory exists
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        json_str = data.model_dump_json(indent=2)
        self.filepath.write_text(json_str + "\n", encoding="utf-8")

    def init(self, name: str = "Default Ledger", base_currency: str = "USD") -> LedgerData:
        """Initialize a new ledger file."""
        if self.filepath.exists():
            raise FileExistsError(f"Ledger file already exists at {self.filepath}")

        data = LedgerData(name=name, base_currency=base_currency)
        self.save(data)
        return data

    def delete(self) -> None:
        """Delete the ledger file."""
        if self.filepath.exists():
            self.filepath.unlink()
