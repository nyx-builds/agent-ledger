"""API Key authentication for agent-ledger REST API.

Provides a simple, self-contained API key management system so agents can
secure programmatic access to the ledger REST API without external auth providers.
Keys are hashed with SHA-256 + salt for storage safety, and support per-key
scopes/roles and rate-limit metadata.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# Scopes supported by the ledger API
VALID_SCOPES = {
    "read",          # GET endpoints
    "write",         # POST/PATCH/DELETE on accounts, entries
    "reports",       # Report generation
    "admin",         # Everything including init, period close, key management
    "reconcile",     # Reconciliation operations
    "budget",        # Budget management
}


@dataclass
class APIKey:
    """Represents an API key for ledger access."""
    id: str
    name: str
    key_prefix: str              # First 8 chars for display (e.g., "agl_abc1…")
    key_hash: str                # SHA-256 hash of the full key with salt
    scopes: list[str] = field(default_factory=lambda: ["read"])
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    active: bool = True
    created_by: str = "system"
    description: str = ""
    # Rate limiting metadata
    rate_limit_per_hour: Optional[int] = None
    request_count: int = 0

    @property
    def is_valid(self) -> bool:
        """Check if this key is currently valid (active and not expired)."""
        if not self.active:
            return False
        if self.expires_at is not None:
            if datetime.now(timezone.utc) > self.expires_at:
                return False
        return True

    def has_scope(self, scope: str) -> bool:
        """Check if the key has a particular scope. Admin scope implies all."""
        if "admin" in self.scopes:
            return True
        return scope in self.scopes


class APIKeyManager:
    """Manages API keys for the ledger, stored in ledger metadata."""

    # Key format: agl_<32 hex chars> = agl_ + 16 bytes
    KEY_PREFIX_STR = "agl_"
    KEY_HEX_LENGTH = 32

    def __init__(self, ledger=None):
        """Initialize with an optional Ledger instance.

        Can also be used standalone by storing keys in a dict.
        """
        self.ledger = ledger

    def _ensure_store(self) -> None:
        """Ensure the ledger metadata has the api_keys structure."""
        if self.ledger is None:
            return
        meta = self.ledger.data.metadata
        if "api_keys" not in meta:
            meta["api_keys"] = []

    def _get_keys_store(self) -> list[dict]:
        """Get the raw keys list from storage."""
        if self.ledger:
            self._ensure_store()
            return self.ledger.data.metadata["api_keys"]
        return getattr(self, "_standalone_keys", [])

    def _save_keys_store(self, keys: list[dict]) -> None:
        """Save the keys list back to storage."""
        if self.ledger:
            self.ledger.data.metadata["api_keys"] = keys
            self.ledger.save()
        else:
            self._standalone_keys = keys

    @staticmethod
    def _hash_key(raw_key: str) -> str:
        """Hash a raw API key with a salt for secure storage."""
        salt = "agent-ledger-v1-salt"
        return hashlib.sha256(f"{salt}:{raw_key}".encode()).hexdigest()

    @classmethod
    def generate_key(cls) -> str:
        """Generate a new random API key string."""
        return cls.KEY_PREFIX_STR + secrets.token_hex(cls.KEY_HEX_LENGTH // 2)

    def create_key(
        self,
        name: str,
        scopes: Optional[list[str]] = None,
        description: str = "",
        created_by: str = "system",
        expires_at: Optional[datetime] = None,
        rate_limit_per_hour: Optional[int] = None,
    ) -> tuple[APIKey, str]:
        """Create a new API key.

        Args:
            name: Human-readable name for the key
            scopes: List of scopes (default: ['read'])
            description: Optional description
            created_by: Who created the key
            expires_at: Optional expiration datetime
            rate_limit_per_hour: Optional request limit per hour

        Returns:
            Tuple of (APIKey object, raw_key_string) — the raw key is only
            available at creation time and should be stored securely by the caller.
        """
        if not name or not name.strip():
            raise ValueError("Key name must not be empty")

        # Validate scopes
        final_scopes = scopes or ["read"]
        for s in final_scopes:
            if s not in VALID_SCOPES:
                raise ValueError(f"Invalid scope '{s}'. Valid scopes: {sorted(VALID_SCOPES)}")

        raw_key = self.generate_key()
        key_hash = self._hash_key(raw_key)
        key_prefix = raw_key[:12] + "…"

        api_key = APIKey(
            id=str(uuid.uuid4()),
            name=name.strip(),
            key_prefix=key_prefix,
            key_hash=key_hash,
            scopes=final_scopes,
            description=description,
            created_by=created_by,
            expires_at=expires_at,
            rate_limit_per_hour=rate_limit_per_hour,
        )

        keys = self._get_keys_store()
        keys.append(self._key_to_dict(api_key))
        self._save_keys_store(keys)

        return api_key, raw_key

    def validate_key(self, raw_key: str) -> Optional[APIKey]:
        """Validate a raw API key string.

        Returns the APIKey if valid, None if invalid/inactive/expired.
        Also updates last_used and request_count.
        """
        if not raw_key or not raw_key.startswith(self.KEY_PREFIX_STR):
            return None

        key_hash = self._hash_key(raw_key)
        keys = self._get_keys_store()

        for i, kd in enumerate(keys):
            if kd["key_hash"] == key_hash:
                api_key = self._dict_to_key(kd)
                if not api_key.is_valid:
                    return None
                # Update usage stats
                kd["last_used"] = datetime.now(timezone.utc).isoformat()
                kd["request_count"] = kd.get("request_count", 0) + 1
                keys[i] = kd
                self._save_keys_store(keys)
                return api_key

        return None

    def get_key(self, key_id: str) -> APIKey:
        """Get a key by ID (for management, not auth)."""
        keys = self._get_keys_store()
        for kd in keys:
            if kd["id"] == key_id:
                return self._dict_to_key(kd)
        raise KeyError(f"API key '{key_id}' not found")

    def list_keys(
        self,
        active_only: bool = False,
        scope: Optional[str] = None,
    ) -> list[APIKey]:
        """List all API keys (without exposing raw key values)."""
        keys = self._get_keys_store()
        result = []
        for kd in keys:
            api_key = self._dict_to_key(kd)
            if active_only and not api_key.is_valid:
                continue
            if scope and not api_key.has_scope(scope):
                continue
            result.append(api_key)
        return sorted(result, key=lambda k: k.created_at, reverse=True)

    def revoke_key(self, key_id: str) -> APIKey:
        """Revoke an API key by setting active=False."""
        keys = self._get_keys_store()
        for i, kd in enumerate(keys):
            if kd["id"] == key_id:
                kd["active"] = False
                keys[i] = kd
                self._save_keys_store(keys)
                return self._dict_to_key(kd)
        raise KeyError(f"API key '{key_id}' not found")

    def delete_key(self, key_id: str) -> None:
        """Permanently delete an API key."""
        keys = self._get_keys_store()
        for i, kd in enumerate(keys):
            if kd["id"] == key_id:
                keys.pop(i)
                self._save_keys_store(keys)
                return
        raise KeyError(f"API key '{key_id}' not found")

    def update_key(
        self,
        key_id: str,
        scopes: Optional[list[str]] = None,
        description: Optional[str] = None,
        active: Optional[bool] = None,
        expires_at: Optional[datetime] = None,
        rate_limit_per_hour: Optional[int] = None,
    ) -> APIKey:
        """Update an existing API key's properties."""
        if scopes is not None:
            for s in scopes:
                if s not in VALID_SCOPES:
                    raise ValueError(f"Invalid scope '{s}'. Valid scopes: {sorted(VALID_SCOPES)}")

        keys = self._get_keys_store()
        for i, kd in enumerate(keys):
            if kd["id"] == key_id:
                if scopes is not None:
                    kd["scopes"] = scopes
                if description is not None:
                    kd["description"] = description
                if active is not None:
                    kd["active"] = active
                if expires_at is not None:
                    kd["expires_at"] = expires_at.isoformat()
                elif expires_at is None and "expires_at" in kd and kd.get("_clear_expiry"):
                    kd.pop("expires_at", None)
                if rate_limit_per_hour is not None:
                    kd["rate_limit_per_hour"] = rate_limit_per_hour
                keys[i] = kd
                self._save_keys_store(keys)
                return self._dict_to_key(kd)
        raise KeyError(f"API key '{key_id}' not found")

    def check_rate_limit(self, key_id: str) -> tuple[bool, int]:
        """Check if a key has exceeded its rate limit.

        Returns (allowed, remaining_requests_this_hour).
        This is a simple counter-based check; production deployments should
        use a proper rate limiter (e.g., Redis token bucket).
        """
        try:
            api_key = self.get_key(key_id)
        except KeyError:
            return False, 0

        if api_key.rate_limit_per_hour is None:
            return True, -1  # unlimited

        # Count requests in the last hour
        keys = self._get_keys_store()
        for kd in keys:
            if kd["id"] == key_id:
                count = kd.get("request_count", 0)
                # Reset counter if last_used was > 1 hour ago
                last_used_str = kd.get("last_used")
                if last_used_str:
                    last_used = datetime.fromisoformat(last_used_str)
                    if (datetime.now(timezone.utc) - last_used).total_seconds() > 3600:
                        kd["request_count"] = 0
                        keys[keys.index(kd)] = kd
                        self._save_keys_store(keys)
                        count = 0
                remaining = max(0, api_key.rate_limit_per_hour - count)
                return count < api_key.rate_limit_per_hour, remaining

        return False, 0

    # ── Serialization ────────────────────────────────────────────

    @staticmethod
    def _key_to_dict(key: APIKey) -> dict:
        return {
            "id": key.id,
            "name": key.name,
            "key_prefix": key.key_prefix,
            "key_hash": key.key_hash,
            "scopes": key.scopes,
            "created_at": key.created_at.isoformat(),
            "last_used": key.last_used.isoformat() if key.last_used else None,
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            "active": key.active,
            "created_by": key.created_by,
            "description": key.description,
            "rate_limit_per_hour": key.rate_limit_per_hour,
            "request_count": key.request_count,
        }

    @staticmethod
    def _dict_to_key(d: dict) -> APIKey:
        return APIKey(
            id=d["id"],
            name=d["name"],
            key_prefix=d.get("key_prefix", ""),
            key_hash=d["key_hash"],
            scopes=d.get("scopes", ["read"]),
            created_at=datetime.fromisoformat(d.get("created_at", datetime.now(timezone.utc).isoformat())),
            last_used=datetime.fromisoformat(d["last_used"]) if d.get("last_used") else None,
            expires_at=datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None,
            active=d.get("active", True),
            created_by=d.get("created_by", "system"),
            description=d.get("description", ""),
            rate_limit_per_hour=d.get("rate_limit_per_hour"),
            request_count=d.get("request_count", 0),
        )
