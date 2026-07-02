"""Balance alert rules for agent-ledger — threshold-based monitoring and notifications.

Agents can set up rules that trigger when an account balance crosses a threshold
(high or low). When checked, rules that are violated produce AlertTrigger records
that can be polled, sent to webhooks, or reviewed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .exceptions import LedgerError
from .ledger import Ledger


class AlertCondition(str, Enum):
    """Conditions that can trigger an alert."""
    ABOVE = "above"          # balance > threshold
    BELOW = "below"          # balance < threshold
    EQUALS = "equals"        # balance == threshold (within tolerance)
    CHANGED = "changed"      # balance changed since last check


class AlertSeverity(str, Enum):
    """Severity levels for alerts."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertRule:
    """A balance monitoring rule."""
    id: str
    name: str
    account_code: str
    condition: AlertCondition
    threshold: float
    severity: AlertSeverity = AlertSeverity.WARNING
    enabled: bool = True
    cooldown_minutes: int = 60
    description: str = ""
    last_triggered: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AlertTrigger:
    """An alert that was triggered by a rule check."""
    id: str
    rule_id: str
    rule_name: str
    account_code: str
    condition: str
    threshold: float
    actual_value: float
    severity: str
    message: str
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = False


class AlertManager:
    """Manages balance alert rules and triggers for a ledger."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def _ensure_alerts_store(self) -> None:
        """Ensure the metadata has the alerts structures."""
        meta = self.ledger.data.metadata
        if "alert_rules" not in meta:
            meta["alert_rules"] = []
        if "alert_triggers" not in meta:
            meta["alert_triggers"] = []

    def create_rule(
        self,
        name: str,
        account_code: str,
        condition: AlertCondition,
        threshold: float,
        severity: AlertSeverity = AlertSeverity.WARNING,
        description: str = "",
        cooldown_minutes: int = 60,
    ) -> AlertRule:
        """Create a new alert rule.

        Args:
            name: Human-readable rule name
            account_code: Account to monitor
            condition: When to trigger (above, below, equals, changed)
            threshold: Threshold value for the condition
            severity: Alert severity level
            description: Optional description
            cooldown_minutes: Minimum minutes between triggers of the same rule

        Returns:
            The created AlertRule

        Raises:
            AccountNotFoundError: If the account doesn't exist
            ValueError: If parameters are invalid
        """
        # Validate account exists
        self.ledger.get_account(account_code)

        if not name or not name.strip():
            raise ValueError("Rule name must not be empty")
        if cooldown_minutes < 0:
            raise ValueError("cooldown_minutes must be >= 0")

        self._ensure_alerts_store()

        rule = AlertRule(
            id=str(uuid.uuid4()),
            name=name.strip(),
            account_code=account_code.strip().lower(),
            condition=AlertCondition(condition) if isinstance(condition, str) else condition,
            threshold=float(threshold),
            severity=AlertSeverity(severity) if isinstance(severity, str) else severity,
            description=description,
            cooldown_minutes=cooldown_minutes,
        )

        self.ledger.data.metadata["alert_rules"].append(self._rule_to_dict(rule))
        self.ledger.save()
        return rule

    def get_rule(self, rule_id: str) -> AlertRule:
        """Get a rule by ID."""
        self._ensure_alerts_store()
        for rd in self.ledger.data.metadata["alert_rules"]:
            if rd["id"] == rule_id:
                return self._dict_to_rule(rd)
        raise LedgerError(f"Alert rule '{rule_id}' not found")

    def list_rules(
        self,
        account_code: Optional[str] = None,
        enabled_only: bool = False,
        severity: Optional[AlertSeverity] = None,
    ) -> list[AlertRule]:
        """List alert rules with optional filters."""
        self._ensure_alerts_store()
        rules = []
        for rd in self.ledger.data.metadata["alert_rules"]:
            rule = self._dict_to_rule(rd)
            if account_code and rule.account_code != account_code.strip().lower():
                continue
            if enabled_only and not rule.enabled:
                continue
            if severity and rule.severity != severity:
                continue
            rules.append(rule)
        return sorted(rules, key=lambda r: r.created_at)

    def update_rule(
        self,
        rule_id: str,
        threshold: Optional[float] = None,
        severity: Optional[AlertSeverity] = None,
        enabled: Optional[bool] = None,
        cooldown_minutes: Optional[int] = None,
        description: Optional[str] = None,
    ) -> AlertRule:
        """Update an existing alert rule."""
        self._ensure_alerts_store()
        for i, rd in enumerate(self.ledger.data.metadata["alert_rules"]):
            if rd["id"] == rule_id:
                if threshold is not None:
                    rd["threshold"] = float(threshold)
                if severity is not None:
                    rd["severity"] = severity.value if isinstance(severity, AlertSeverity) else severity
                if enabled is not None:
                    rd["enabled"] = enabled
                if cooldown_minutes is not None:
                    if cooldown_minutes < 0:
                        raise ValueError("cooldown_minutes must be >= 0")
                    rd["cooldown_minutes"] = cooldown_minutes
                if description is not None:
                    rd["description"] = description
                self.ledger.save()
                return self._dict_to_rule(rd)
        raise LedgerError(f"Alert rule '{rule_id}' not found")

    def delete_rule(self, rule_id: str) -> None:
        """Delete an alert rule."""
        self._ensure_alerts_store()
        rules = self.ledger.data.metadata["alert_rules"]
        for i, rd in enumerate(rules):
            if rd["id"] == rule_id:
                rules.pop(i)
                self.ledger.save()
                return
        raise LedgerError(f"Alert rule '{rule_id}' not found")

    def check_rules(self) -> list[AlertTrigger]:
        """Check all enabled rules against current balances.

        Returns a list of AlertTrigger objects for rules that fired.
        Rules within their cooldown period are not re-triggered.
        """
        self._ensure_alerts_store()
        triggers: list[AlertTrigger] = []

        for rule in self.list_rules(enabled_only=True):
            try:
                balance = self.ledger.get_account_balance(rule.account_code)
            except Exception:
                continue

            actual = balance.balance
            fired = False

            if rule.condition == AlertCondition.ABOVE:
                fired = actual > rule.threshold
            elif rule.condition == AlertCondition.BELOW:
                fired = actual < rule.threshold
            elif rule.condition == AlertCondition.EQUALS:
                fired = abs(actual - rule.threshold) < 0.01
            elif rule.condition == AlertCondition.CHANGED:
                # Fire if last_triggered is None or balance changed since then
                if rule.last_triggered is None:
                    fired = True
                else:
                    # Store the last-checked value in the rule metadata
                    last_val = self._get_last_value(rule.id)
                    fired = last_val is None or abs(actual - last_val) > 0.01

            if not fired:
                continue

            # Check cooldown
            now = datetime.now(timezone.utc)
            if rule.last_triggered is not None:
                elapsed = (now - rule.last_triggered).total_seconds() / 60.0
                if elapsed < rule.cooldown_minutes:
                    continue

            trigger = AlertTrigger(
                id=str(uuid.uuid4()),
                rule_id=rule.id,
                rule_name=rule.name,
                account_code=rule.account_code,
                condition=rule.condition.value,
                threshold=rule.threshold,
                actual_value=actual,
                severity=rule.severity.value,
                message=(
                    f"Account '{rule.account_code}' balance {actual:.2f} "
                    f"is {rule.condition.value} threshold {rule.threshold:.2f}"
                ),
            )

            triggers.append(trigger)

            # Update rule's last_triggered
            for rd in self.ledger.data.metadata["alert_rules"]:
                if rd["id"] == rule.id:
                    rd["last_triggered"] = now.isoformat()
                    rd["_last_value"] = actual
                    break

        # Persist triggers
        for t in triggers:
            self.ledger.data.metadata["alert_triggers"].append(self._trigger_to_dict(t))

        if triggers:
            self.ledger.save()

        return triggers

    def list_triggers(
        self,
        rule_id: Optional[str] = None,
        acknowledged: Optional[bool] = None,
        severity: Optional[str] = None,
        limit: int = 50,
    ) -> list[AlertTrigger]:
        """List alert triggers with optional filters."""
        self._ensure_alerts_store()
        triggers = []
        for td in self.ledger.data.metadata["alert_triggers"]:
            if rule_id and td["rule_id"] != rule_id:
                continue
            if acknowledged is not None and td["acknowledged"] != acknowledged:
                continue
            if severity and td["severity"] != severity:
                continue
            triggers.append(self._dict_to_trigger(td))

        triggers.sort(key=lambda t: t.triggered_at, reverse=True)
        return triggers[:limit]

    def acknowledge_trigger(self, trigger_id: str) -> AlertTrigger:
        """Mark a trigger as acknowledged."""
        self._ensure_alerts_store()
        for td in self.ledger.data.metadata["alert_triggers"]:
            if td["id"] == trigger_id:
                td["acknowledged"] = True
                self.ledger.save()
                return self._dict_to_trigger(td)
        raise LedgerError(f"Alert trigger '{trigger_id}' not found")

    def acknowledge_all(self, rule_id: Optional[str] = None) -> int:
        """Acknowledge all triggers, optionally filtered by rule_id.

        Returns the count of acknowledged triggers.
        """
        self._ensure_alerts_store()
        count = 0
        for td in self.ledger.data.metadata["alert_triggers"]:
            if td["acknowledged"]:
                continue
            if rule_id and td["rule_id"] != rule_id:
                continue
            td["acknowledged"] = True
            count += 1
        if count:
            self.ledger.save()
        return count

    def clear_triggers(self, acknowledged_only: bool = True) -> int:
        """Clear triggers from history.

        Args:
            acknowledged_only: If True, only clear acknowledged triggers.
                              If False, clear all triggers.

        Returns:
            Count of cleared triggers.
        """
        self._ensure_alerts_store()
        triggers = self.ledger.data.metadata["alert_triggers"]
        before = len(triggers)
        if acknowledged_only:
            self.ledger.data.metadata["alert_triggers"] = [
                t for t in triggers if not t.get("acknowledged", False)
            ]
        else:
            self.ledger.data.metadata["alert_triggers"] = []
        after = len(self.ledger.data.metadata["alert_triggers"])
        if before != after:
            self.ledger.save()
        return before - after

    def _get_last_value(self, rule_id: str) -> Optional[float]:
        """Get the last-checked balance value for a rule."""
        for rd in self.ledger.data.metadata["alert_rules"]:
            if rd["id"] == rule_id:
                return rd.get("_last_value")
        return None

    # ── Serialization ────────────────────────────────────────────

    @staticmethod
    def _rule_to_dict(rule: AlertRule) -> dict:
        return {
            "id": rule.id,
            "name": rule.name,
            "account_code": rule.account_code,
            "condition": rule.condition.value,
            "threshold": rule.threshold,
            "severity": rule.severity.value,
            "enabled": rule.enabled,
            "cooldown_minutes": rule.cooldown_minutes,
            "description": rule.description,
            "last_triggered": rule.last_triggered.isoformat() if rule.last_triggered else None,
            "created_at": rule.created_at.isoformat(),
        }

    @staticmethod
    def _dict_to_rule(d: dict) -> AlertRule:
        return AlertRule(
            id=d["id"],
            name=d["name"],
            account_code=d["account_code"],
            condition=AlertCondition(d["condition"]),
            threshold=d["threshold"],
            severity=AlertSeverity(d["severity"]),
            enabled=d["enabled"],
            cooldown_minutes=d.get("cooldown_minutes", 60),
            description=d.get("description", ""),
            last_triggered=datetime.fromisoformat(d["last_triggered"]) if d.get("last_triggered") else None,
            created_at=datetime.fromisoformat(d.get("created_at", datetime.now(timezone.utc).isoformat())),
        )

    @staticmethod
    def _trigger_to_dict(t: AlertTrigger) -> dict:
        return {
            "id": t.id,
            "rule_id": t.rule_id,
            "rule_name": t.rule_name,
            "account_code": t.account_code,
            "condition": t.condition,
            "threshold": t.threshold,
            "actual_value": t.actual_value,
            "severity": t.severity,
            "message": t.message,
            "triggered_at": t.triggered_at.isoformat(),
            "acknowledged": t.acknowledged,
        }

    @staticmethod
    def _dict_to_trigger(d: dict) -> AlertTrigger:
        return AlertTrigger(
            id=d["id"],
            rule_id=d["rule_id"],
            rule_name=d["rule_name"],
            account_code=d["account_code"],
            condition=d["condition"],
            threshold=d["threshold"],
            actual_value=d["actual_value"],
            severity=d["severity"],
            message=d["message"],
            triggered_at=datetime.fromisoformat(d["triggered_at"]),
            acknowledged=d.get("acknowledged", False),
        )
