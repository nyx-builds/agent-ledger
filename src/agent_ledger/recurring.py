"""Recurring journal entries for agent-ledger.

Define templates that generate journal entries on a schedule.  An autonomous
agent can create a recurring template once (e.g. "monthly office rent") and the
template will produce balanced journal entries on the correct cadence without
further intervention.

Schedule types
--------------
* ``daily``    — fires every *N* days
* ``weekly``   — fires every *N* weeks on a given weekday
* ``monthly``  — fires on a given day-of-month every *N* months
* ``yearly``   — fires on a given month/day every *N* years
* ``quarterly``— convenience shortcut for every 3 months

Templates are stored inside ``LedgerData.metadata["recurring_entries"]`` so they
survive save/load round-trips with both JSON and SQLite backends.
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from .models import JournalLine
from .ledger import Ledger
from .exceptions import LedgerError


class RecurringError(LedgerError):
    """Error related to recurring entries."""


class RecurringNotFoundError(RecurringError):
    """Recurring template not found."""


class ScheduleType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


# ── Data structures ──────────────────────────────────────────────


@dataclass
class RecurringLine:
    """A line template for a recurring entry."""
    account_code: str
    debit: float = 0.0
    credit: float = 0.0
    description: str = ""


@dataclass
class RecurringEntry:
    """A template that generates journal entries on a schedule.

    Attributes:
        id: Unique identifier.
        name: Human-readable name.
        description: Description used for generated entries.
        lines: Template lines (account_code, debit, credit).
        schedule_type: daily / weekly / monthly / quarterly / yearly.
        interval: Every *N* periods (e.g. interval=2 monthly = every 2 months).
        day_of_month: Day of month for monthly/quarterly/yearly (1-31).
        day_of_week: Day of week for weekly (0=Monday … 6=Sunday).
        month_of_year: Month for yearly schedule (1-12).
        start_date: When the schedule begins.
        end_date: Optional end date (None = forever).
        max_occurrences: Optional cap on number of generated entries.
        tags: Tags applied to each generated entry.
        metadata: Extra metadata for generated entries.
        active: Whether the template is active.
        occurrences_created: Number of entries generated so far.
        last_run: Timestamp of the most recent generation.
        next_run: Timestamp of the next scheduled generation.
        created_at: When the template was created.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    lines: list[RecurringLine] = field(default_factory=list)
    schedule_type: ScheduleType = ScheduleType.MONTHLY
    interval: int = 1
    day_of_month: int = 1
    day_of_week: int = 0  # Monday
    month_of_year: int = 1
    start_date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_date: Optional[datetime] = None
    max_occurrences: Optional[int] = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    active: bool = True
    occurrences_created: int = 0
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Schedule computation ─────────────────────────────────────────


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def compute_next_run(
    schedule_type: ScheduleType,
    interval: int,
    after: datetime,
    day_of_month: int = 1,
    day_of_week: int = 0,
    month_of_year: int = 1,
) -> datetime:
    """Compute the next scheduled run after a given datetime.

    Args:
        schedule_type: Type of schedule.
        interval: Every *N* periods.
        after: Compute the next run strictly after this datetime.
        day_of_month: Target day for monthly/quarterly/yearly (clamped to month length).
        day_of_week: Target weekday for weekly (0=Mon … 6=Sun).
        month_of_year: Target month for yearly.

    Returns:
        The next scheduled datetime (timezone-aware, at 00:00 local).
    """
    after = _ensure_aware(after)
    interval = max(1, interval)

    if schedule_type == ScheduleType.DAILY:
        return after.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=interval)

    elif schedule_type == ScheduleType.WEEKLY:
        base = after.replace(hour=0, minute=0, second=0, microsecond=0)
        days_ahead = (day_of_week - base.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # always move forward
        candidate = base + timedelta(days=days_ahead)
        # Skip by full weeks for interval > 1
        if interval > 1:
            candidate += timedelta(weeks=interval - 1)
        return candidate

    elif schedule_type == ScheduleType.MONTHLY:
        base_year = after.year
        base_month = after.month
        # Start from the month *after* `after`
        m = base_month
        y = base_year
        m += 1
        while m > 12:
            m -= 12
            y += 1

        for _ in range(12 * interval + 12):  # safety bound
            dom = min(day_of_month, calendar.monthrange(y, m)[1])
            candidate = datetime(y, m, dom, 0, 0, 0, tzinfo=timezone.utc)
            if candidate > after:
                return candidate
            m += 1
            while m > 12:
                m -= 12
                y += 1

        # Fallback (should not reach)
        return after + timedelta(days=30 * interval)

    elif schedule_type == ScheduleType.QUARTERLY:
        # Quarterly = every 3 months with the given interval
        base_year = after.year
        base_month = after.month
        m = base_month + 3
        while m > 12:
            m -= 12
            base_year += 1
        for _ in range(24 * interval + 24):
            dom = min(day_of_month, calendar.monthrange(base_year, m)[1])
            candidate = datetime(base_year, m, dom, 0, 0, 0, tzinfo=timezone.utc)
            if candidate > after:
                return candidate
            m += 3
            while m > 12:
                m -= 12
                base_year += 1
        return after + timedelta(days=90 * interval)

    elif schedule_type == ScheduleType.YEARLY:
        y = after.year + 1
        for _ in range(interval + 5):
            dom = min(day_of_month, calendar.monthrange(y, month_of_year)[1])
            candidate = datetime(y, month_of_year, dom, 0, 0, 0, tzinfo=timezone.utc)
            if candidate > after:
                return candidate
            y += 1
        return after + timedelta(days=365 * interval)

    else:
        raise RecurringError(f"Unknown schedule type: {schedule_type}")


# ── Manager ──────────────────────────────────────────────────────


class RecurringManager:
    """Create, list, and process recurring entry templates."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self._templates: list[RecurringEntry] = []
        self._load()

    # ── Persistence ─────────────────────────────────────────────

    def _load(self) -> None:
        """Load templates from ledger metadata."""
        raw = self.ledger.data.metadata.get("recurring_entries", [])
        for rd in raw:
            lines = [
                RecurringLine(
                    account_code=l.get("account_code", ""),
                    debit=l.get("debit", 0.0),
                    credit=l.get("credit", 0.0),
                    description=l.get("description", ""),
                )
                for l in rd.get("lines", [])
            ]
            template = RecurringEntry(
                id=rd.get("id", str(uuid.uuid4())),
                name=rd.get("name", ""),
                description=rd.get("description", ""),
                lines=lines,
                schedule_type=ScheduleType(rd.get("schedule_type", "monthly")),
                interval=rd.get("interval", 1),
                day_of_month=rd.get("day_of_month", 1),
                day_of_week=rd.get("day_of_week", 0),
                month_of_year=rd.get("month_of_year", 1),
                start_date=_parse_dt(rd.get("start_date")) or datetime.now(timezone.utc),
                end_date=_parse_dt(rd.get("end_date")),
                max_occurrences=rd.get("max_occurrences"),
                tags=rd.get("tags", []),
                metadata=rd.get("metadata", {}),
                active=rd.get("active", True),
                occurrences_created=rd.get("occurrences_created", 0),
                last_run=_parse_dt(rd.get("last_run")),
                next_run=_parse_dt(rd.get("next_run")),
                created_at=_parse_dt(rd.get("created_at")) or datetime.now(timezone.utc),
            )
            self._templates.append(template)

    def _save(self) -> None:
        """Persist templates to ledger metadata."""
        data = []
        for t in self._templates:
            data.append({
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "lines": [
                    {
                        "account_code": l.account_code,
                        "debit": l.debit,
                        "credit": l.credit,
                        "description": l.description,
                    }
                    for l in t.lines
                ],
                "schedule_type": t.schedule_type.value,
                "interval": t.interval,
                "day_of_month": t.day_of_month,
                "day_of_week": t.day_of_week,
                "month_of_year": t.month_of_year,
                "start_date": t.start_date.isoformat() if t.start_date else None,
                "end_date": t.end_date.isoformat() if t.end_date else None,
                "max_occurrences": t.max_occurrences,
                "tags": t.tags,
                "metadata": t.metadata,
                "active": t.active,
                "occurrences_created": t.occurrences_created,
                "last_run": t.last_run.isoformat() if t.last_run else None,
                "next_run": t.next_run.isoformat() if t.next_run else None,
                "created_at": t.created_at.isoformat(),
            })
        self.ledger.data.metadata["recurring_entries"] = data
        self.ledger.save()

    # ── CRUD ─────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        description: str,
        lines: list[dict],
        schedule_type: str | ScheduleType = "monthly",
        interval: int = 1,
        day_of_month: int = 1,
        day_of_week: int = 0,
        month_of_year: int = 1,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_occurrences: Optional[int] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> RecurringEntry:
        """Create a new recurring entry template.

        Args:
            name: Display name.
            description: Description for generated entries.
            lines: List of dicts with account_code, debit, credit keys.
            schedule_type: daily / weekly / monthly / quarterly / yearly.
            interval: Every *N* periods.
            day_of_month: Day of month (1-31) for monthly/quarterly/yearly.
            day_of_week: Weekday (0=Mon) for weekly.
            month_of_year: Month (1-12) for yearly.
            start_date: When the schedule starts (default: now).
            end_date: Optional end date.
            max_occurrences: Optional cap on generations.
            tags: Tags for generated entries.
            metadata: Metadata for generated entries.

        Returns:
            The created RecurringEntry.
        """
        if isinstance(schedule_type, str):
            schedule_type = ScheduleType(schedule_type)

        if interval < 1:
            raise RecurringError("Interval must be >= 1")

        if not lines or len(lines) < 2:
            raise RecurringError("At least 2 lines are required")

        # Validate accounts exist and check balance
        total_debit = 0.0
        total_credit = 0.0
        line_objs = []
        for ld in lines:
            code = ld["account_code"].strip().lower()
            self.ledger.get_account(code)  # raises if not found
            d = float(ld.get("debit", 0))
            c = float(ld.get("credit", 0))
            total_debit += d
            total_credit += c
            line_objs.append(RecurringLine(
                account_code=code,
                debit=d,
                credit=c,
                description=ld.get("description", ""),
            ))

        if abs(total_debit - total_credit) > 0.01:
            raise RecurringError(
                f"Template lines do not balance: debits={total_debit:.2f}, "
                f"credits={total_credit:.2f}"
            )

        now = datetime.now(timezone.utc)
        sd = _ensure_aware(start_date) if start_date else now

        template = RecurringEntry(
            name=name,
            description=description,
            lines=line_objs,
            schedule_type=schedule_type,
            interval=interval,
            day_of_month=day_of_month,
            day_of_week=day_of_week,
            month_of_year=month_of_year,
            start_date=sd,
            end_date=_ensure_aware(end_date) if end_date else None,
            max_occurrences=max_occurrences,
            tags=tags or [],
            metadata=metadata or {},
            active=True,
            occurrences_created=0,
            last_run=None,
            next_run=compute_next_run(
                schedule_type, interval, sd,
                day_of_month=day_of_month,
                day_of_week=day_of_week,
                month_of_year=month_of_year,
            ),
        )
        self._templates.append(template)
        self._save()
        return template

    def get(self, template_id: str) -> RecurringEntry:
        for t in self._templates:
            if t.id == template_id:
                return t
        raise RecurringNotFoundError(f"Recurring template '{template_id}' not found")

    def list_templates(
        self,
        active_only: bool = False,
    ) -> list[RecurringEntry]:
        """List all recurring templates."""
        templates = list(self._templates)
        if active_only:
            templates = [t for t in templates if t.active]
        return sorted(templates, key=lambda t: t.created_at)

    def update(
        self,
        template_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        active: Optional[bool] = None,
        end_date: Optional[datetime] = None,
        max_occurrences: Optional[int] = None,
    ) -> RecurringEntry:
        """Update a recurring template.

        Note: schedule and line changes require recreating the template.
        """
        template = self.get(template_id)
        if name is not None:
            template.name = name
        if description is not None:
            template.description = description
        if active is not None:
            template.active = active
        if end_date is not None:
            template.end_date = _ensure_aware(end_date)
        if max_occurrences is not None:
            template.max_occurrences = max_occurrences
        self._save()
        return template

    def delete(self, template_id: str) -> None:
        template = self.get(template_id)
        self._templates.remove(template)
        self._save()

    def pause(self, template_id: str) -> RecurringEntry:
        """Pause a template (set active=False)."""
        return self.update(template_id, active=False)

    def resume(self, template_id: str) -> RecurringEntry:
        """Resume a paused template."""
        return self.update(template_id, active=True)

    # ── Processing ───────────────────────────────────────────────

    def is_due(
        self,
        template: RecurringEntry,
        as_of: Optional[datetime] = None,
    ) -> bool:
        """Check if a template is due to generate an entry."""
        if not template.active:
            return False
        if template.next_run is None:
            return False
        as_of = _ensure_aware(as_of) if as_of else datetime.now(timezone.utc)
        if template.next_run > as_of:
            return False
        if template.end_date and template.next_run > template.end_date:
            return False
        if template.max_occurrences and template.occurrences_created >= template.max_occurrences:
            return False
        return True

    def generate(self, template_id: str, as_of: Optional[datetime] = None) -> Optional[Any]:
        """Generate a journal entry for a single template if it's due.

        Returns the JournalEntry if created, or None if not due.
        """
        template = self.get(template_id)
        if not self.is_due(template, as_of):
            return None

        as_of = _ensure_aware(as_of) if as_of else datetime.now(timezone.utc)

        # Build JournalLine objects
        lines = [
            JournalLine(
                account_code=l.account_code,
                debit=l.debit,
                credit=l.credit,
                description=l.description,
            )
            for l in template.lines
        ]

        entry = self.ledger.post_entry(
            description=template.description or template.name,
            lines=lines,
            tags=template.tags + [f"recurring:{template.id}"],
            timestamp=as_of,
            metadata={
                **template.metadata,
                "recurring_template_id": template.id,
                "recurring_occurrence": template.occurrences_created + 1,
            },
        )

        template.occurrences_created += 1
        template.last_run = as_of

        # Check terminal conditions
        reached_max = (
            template.max_occurrences is not None
            and template.occurrences_created >= template.max_occurrences
        )
        next_candidate = compute_next_run(
            template.schedule_type,
            template.interval,
            as_of,
            day_of_month=template.day_of_month,
            day_of_week=template.day_of_week,
            month_of_year=template.month_of_year,
        )

        if reached_max or (template.end_date and next_candidate > template.end_date):
            template.next_run = None
            template.active = False
        else:
            template.next_run = next_candidate

        self._save()
        return entry

    def process_all(self, as_of: Optional[datetime] = None) -> list[dict]:
        """Process all due templates.

        Args:
            as_of: Process entries due as of this datetime (default: now).

        Returns:
            List of dicts with template_id, template_name, entry_id (or None if skipped).
        """
        as_of = _ensure_aware(as_of) if as_of else datetime.now(timezone.utc)
        results = []

        for template in list(self._templates):
            if not self.is_due(template, as_of):
                results.append({
                    "template_id": template.id,
                    "template_name": template.name,
                    "entry_id": None,
                    "status": "not_due",
                })
                continue

            try:
                entry = self.generate(template.id, as_of=as_of)
                results.append({
                    "template_id": template.id,
                    "template_name": template.name,
                    "entry_id": entry.id if entry else None,
                    "status": "generated" if entry else "not_due",
                })
            except Exception as e:
                results.append({
                    "template_id": template.id,
                    "template_name": template.name,
                    "entry_id": None,
                    "status": "error",
                    "error": str(e),
                })

        return results

    def preview_next(self, template_id: str) -> Optional[datetime]:
        """Get the next scheduled run for a template."""
        template = self.get(template_id)
        return template.next_run


# ── Helpers ──────────────────────────────────────────────────────


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _ensure_aware(dt)
    except (ValueError, AttributeError):
        return None


def format_recurring_list(templates: list[RecurringEntry]) -> str:
    """Format a list of recurring templates as text."""
    if not templates:
        return "No recurring templates found."

    lines = []
    lines.append(f"{'Name':<25} {'Schedule':<15} {'Active':<8} {'Next Run':<22} {'Created':<10}")
    lines.append("-" * 82)

    for t in templates:
        sched = f"{t.schedule_type.value} x{t.interval}"
        active = "Yes" if t.active else "No"
        next_run = t.next_run.strftime("%Y-%m-%d %H:%M") if t.next_run else "—"
        created = t.created_at.strftime("%Y-%m-%d")
        lines.append(
            f"{t.name:<25} {sched:<15} {active:<8} {next_run:<22} {created:<10}"
        )

    return "\n".join(lines)


def format_recurring_detail(template: RecurringEntry) -> str:
    """Format a single recurring template as text."""
    lines = []
    lines.append(f"RECURRING ENTRY: {template.name}")
    lines.append(f"ID: {template.id}")
    lines.append(f"Description: {template.description}")
    lines.append(f"Schedule: {template.schedule_type.value} (interval={template.interval})")
    lines.append(f"Active: {'Yes' if template.active else 'No'}")
    lines.append(f"Occurrences created: {template.occurrences_created}")
    if template.max_occurrences:
        lines.append(f"Max occurrences: {template.max_occurrences}")
    if template.start_date:
        lines.append(f"Start date: {template.start_date.strftime('%Y-%m-%d')}")
    if template.end_date:
        lines.append(f"End date: {template.end_date.strftime('%Y-%m-%d')}")
    if template.last_run:
        lines.append(f"Last run: {template.last_run.strftime('%Y-%m-%d %H:%M')}")
    if template.next_run:
        lines.append(f"Next run: {template.next_run.strftime('%Y-%m-%d %H:%M')}")
    if template.tags:
        lines.append(f"Tags: {', '.join(template.tags)}")
    lines.append("")
    lines.append("Lines:")
    lines.append(f"  {'Account':<15} {'Debit':>12} {'Credit':>12}")
    for l in template.lines:
        lines.append(f"  {l.account_code:<15} {l.debit:>12,.2f} {l.credit:>12,.2f}")

    return "\n".join(lines)
