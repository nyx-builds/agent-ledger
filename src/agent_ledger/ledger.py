"""Ledger engine — core business logic for agent-ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, Union

from .models import (
    Account, AccountType, AccountBalance,
    JournalEntry, JournalLine, ExchangeRate, LedgerData,
)
from .storage import Storage
from .currency import CurrencyConverter
from .audit import AuditLog, AuditAction
from .exceptions import (
    AccountNotFoundError, AccountAlreadyExistsError,
    EntryDoesNotBalanceError, InvalidAccountTypeError,
    CannotDeleteAccountError, CurrencyMismatchError,
    JournalEntryNotFoundError, ReconciliationError,
    AccountHasChildrenError,
)

if TYPE_CHECKING:
    from .sqlite_storage import SQLiteStorage


class Ledger:
    """The main ledger engine managing accounts, entries, and business rules."""

    @staticmethod
    def _ensure_aware(dt: datetime) -> datetime:
        """Ensure a datetime is timezone-aware (assume UTC if naive)."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def __init__(self, storage: Union["Storage", "SQLiteStorage"]):
        self._storage = storage
        self._data: Optional[LedgerData] = None
        self._audit = AuditLog()

    @property
    def data(self) -> LedgerData:
        if self._data is None:
            self._data = self._storage.load()
            # Load audit log from persisted data
            if self._data.audit_log and self._data.audit_log.entries:
                self._audit.from_dict_list(self._data.audit_log.entries)
        return self._data

    @property
    def audit(self) -> AuditLog:
        """Access the audit log."""
        # Ensure data is loaded
        _ = self.data
        return self._audit

    def reload(self) -> None:
        """Reload data from storage."""
        self._data = self._storage.load()
        if self._data.audit_log and self._data.audit_log.entries:
            self._audit = AuditLog()
            self._audit.from_dict_list(self._data.audit_log.entries)

    def save(self) -> None:
        """Save current data to storage."""
        if self._data is not None:
            # Sync audit log to data
            self._data.audit_log.entries = self._audit.to_dict_list()
            self._storage.save(self._data)

    # ── Account Management ──────────────────────────────────────

    def create_account(
        self,
        code: str,
        name: str,
        account_type: AccountType,
        currency: str = "USD",
        description: str = "",
        parent_code: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Account:
        """Create a new account in the chart of accounts."""
        code = code.strip().lower()

        if code in self.data.accounts:
            raise AccountAlreadyExistsError(f"Account '{code}' already exists")

        # Validate parent exists if specified
        if parent_code:
            parent_code = parent_code.strip().lower()
            self.get_account(parent_code)

        account = Account(
            code=code,
            name=name,
            account_type=account_type,
            currency=currency,
            description=description,
            parent_code=parent_code,
            metadata=metadata or {},
        )

        self.data.accounts[code] = account
        self.audit.log(
            action=AuditAction.ACCOUNT_CREATE,
            details={"code": code, "name": name, "type": account_type.value},
            after={"code": code, "name": name, "type": account_type.value},
        )
        self.save()
        return account

    def get_account(self, code: str) -> Account:
        """Get an account by code."""
        code = code.strip().lower()
        if code not in self.data.accounts:
            raise AccountNotFoundError(f"Account '{code}' not found")
        return self.data.accounts[code]

    def update_account(
        self,
        code: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        active: Optional[bool] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> Account:
        """Update an existing account."""
        account = self.get_account(code)
        before = {"name": account.name, "description": account.description, "active": account.active}

        if name is not None:
            account.name = name
        if description is not None:
            account.description = description
        if active is not None:
            account.active = active
        if tags is not None:
            account.tags = tags
        if metadata is not None:
            account.metadata.update(metadata)

        self.audit.log(
            action=AuditAction.ACCOUNT_UPDATE,
            details={"code": code},
            before=before,
            after={"name": account.name, "description": account.description, "active": account.active},
        )
        self.save()
        return account

    def delete_account(self, code: str) -> None:
        """Delete an account if it has zero balance and no children."""
        code = code.strip().lower()
        balance = self.get_account_balance(code)

        # Check for zero balance
        if balance.raw_balance != 0:
            raise CannotDeleteAccountError(
                f"Cannot delete account '{code}' with non-zero balance: {balance.raw_balance}"
            )

        # Check for children
        children = [
            a for a in self.data.accounts.values()
            if a.parent_code == code
        ]
        if children:
            child_codes = [c.code for c in children]
            raise AccountHasChildrenError(
                f"Cannot delete account '{code}' with child accounts: {child_codes}"
            )

        before = {"code": code, "name": self.data.accounts[code].name}
        del self.data.accounts[code]
        self.audit.log(
            action=AuditAction.ACCOUNT_DELETE,
            details={"code": code},
            before=before,
        )
        self.save()

    def list_accounts(
        self,
        account_type: Optional[AccountType] = None,
        active_only: bool = False,
        tag: Optional[str] = None,
    ) -> list[Account]:
        """List all accounts, optionally filtered."""
        accounts = list(self.data.accounts.values())
        if account_type is not None:
            accounts = [a for a in accounts if a.account_type == account_type]
        if active_only:
            accounts = [a for a in accounts if a.active]
        if tag is not None:
            accounts = [a for a in accounts if tag in a.tags]
        return sorted(accounts, key=lambda a: a.code)

    # ── Journal Entry Management ────────────────────────────────

    def post_entry(
        self,
        description: str,
        lines: list[JournalLine],
        tags: Optional[list[str]] = None,
        timestamp: Optional[datetime] = None,
        metadata: Optional[dict] = None,
    ) -> JournalEntry:
        """Post a new journal entry.

        Validates:
        - All referenced accounts exist
        - Entry balances (debits = credits)
        - Currency consistency (all accounts in same currency unless rates exist)
        """
        # Validate accounts exist
        for line in lines:
            self.get_account(line.account_code)

        entry = JournalEntry(
            description=description,
            lines=lines,
            tags=tags or [],
            timestamp=timestamp or datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        self.data.entries.append(entry)
        self.audit.log(
            action=AuditAction.ENTRY_POST,
            details={
                "entry_id": entry.id,
                "description": description,
                "total_debits": entry.total_debits,
                "total_credits": entry.total_credits,
            },
            after={"entry_id": entry.id, "description": description},
        )
        self.save()
        return entry

    def get_entry(self, entry_id: str) -> JournalEntry:
        """Get a journal entry by ID."""
        for entry in self.data.entries:
            if entry.id == entry_id:
                return entry
        raise JournalEntryNotFoundError(f"Journal entry '{entry_id}' not found")

    def delete_entry(self, entry_id: str) -> None:
        """Delete a journal entry by ID."""
        entry = self.get_entry(entry_id)
        if entry.reconciled:
            raise ReconciliationError(
                f"Cannot delete reconciled entry '{entry_id}'"
            )
        self.audit.log(
            action=AuditAction.ENTRY_DELETE,
            details={
                "entry_id": entry_id,
                "description": entry.description,
            },
            before={"entry_id": entry_id, "description": entry.description},
        )
        self.data.entries.remove(entry)
        self.save()

    def list_entries(
        self,
        account_code: Optional[str] = None,
        tag: Optional[str] = None,
        reconciled: Optional[bool] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list[JournalEntry]:
        """List journal entries with optional filters."""
        entries = list(self.data.entries)

        if account_code is not None:
            code = account_code.strip().lower()
            entries = [
                e for e in entries
                if any(line.account_code == code for line in e.lines)
            ]

        if tag is not None:
            entries = [e for e in entries if tag in e.tags]

        if reconciled is not None:
            entries = [e for e in entries if e.reconciled == reconciled]

        if start_date is not None:
            start_aware = self._ensure_aware(start_date)
            entries = [e for e in entries if self._ensure_aware(e.timestamp) >= start_aware]
        if end_date is not None:
            end_aware = self._ensure_aware(end_date)
            entries = [e for e in entries if self._ensure_aware(e.timestamp) <= end_aware]

        return sorted(entries, key=lambda e: e.timestamp)

    def reconcile_entry(self, entry_id: str) -> JournalEntry:
        """Mark a journal entry as reconciled."""
        entry = self.get_entry(entry_id)
        entry.reconciled = True
        self.audit.log(
            action=AuditAction.ENTRY_RECONCILE,
            details={"entry_id": entry_id},
        )
        self.save()
        return entry

    def unreconcile_entry(self, entry_id: str) -> JournalEntry:
        """Mark a journal entry as unreconciled."""
        entry = self.get_entry(entry_id)
        entry.reconciled = False
        self.audit.log(
            action=AuditAction.ENTRY_UNRECONCILE,
            details={"entry_id": entry_id},
        )
        self.save()
        return entry

    # ── Balance Computation ─────────────────────────────────────

    def get_account_balance(self, code: str) -> AccountBalance:
        """Get the current balance for an account."""
        account = self.get_account(code)
        debit_total = 0.0
        credit_total = 0.0

        for entry in self.data.entries:
            for line in entry.lines:
                if line.account_code == code:
                    debit_total += line.debit
                    credit_total += line.credit

        return AccountBalance(
            account_code=code,
            account_name=account.name,
            account_type=account.account_type,
            currency=account.currency,
            debit_total=round(debit_total, 2),
            credit_total=round(credit_total, 2),
        )

    def get_account_transactions(self, code: str) -> list[dict]:
        """Get all transactions for an account with running balance."""
        code = code.strip().lower()
        account = self.get_account(code)
        transactions = []
        running_debit = 0.0
        running_credit = 0.0

        for entry in sorted(self.data.entries, key=lambda e: e.timestamp):
            for line in entry.lines:
                if line.account_code == code:
                    running_debit += line.debit
                    running_credit += line.credit
                    balance = running_debit - running_credit
                    if account.account_type.is_credit_account:
                        balance = running_credit - running_debit
                    transactions.append({
                        "entry_id": entry.id,
                        "timestamp": entry.timestamp,
                        "description": entry.description,
                        "debit": line.debit,
                        "credit": line.credit,
                        "balance": round(balance, 2),
                        "reconciled": entry.reconciled,
                    })

        return transactions

    def get_all_balances(self) -> list[AccountBalance]:
        """Get balances for all accounts."""
        return [
            self.get_account_balance(code)
            for code in self.data.accounts
        ]

    def reverse_entry(self, entry_id: str, reason: Optional[str] = None) -> JournalEntry:
        """Reverse a journal entry by creating an opposing entry.

        Creates a new entry that mirrors the original — debits become credits
        and credits become debits. The original entry is not modified.

        Args:
            entry_id: ID of the entry to reverse
            reason: Optional reason for the reversal

        Returns:
            The new reversal entry

        Raises:
            JournalEntryNotFoundError: If the entry doesn't exist
        """
        original = self.get_entry(entry_id)

        # Create reversing lines
        reversing_lines = [
            JournalLine(
                account_code=line.account_code,
                debit=line.credit,  # Swap debit/credit
                credit=line.debit,
                description=f"Reversal: {line.description}" if line.description else "Reversal",
            )
            for line in original.lines
        ]

        description = reason or f"Reversal of entry: {original.description}"

        reversal = self.post_entry(
            description=description,
            lines=reversing_lines,
            tags=original.tags + ["reversal"],
            metadata={
                "reversal_of": original.id,
                "reversal_reason": reason or "",
            },
        )

        self.audit.log(
            action=AuditAction.ENTRY_REVERSE,
            details={
                "original_entry_id": entry_id,
                "reversal_entry_id": reversal.id,
                "reason": reason or "",
            },
            before={"entry_id": entry_id},
            after={"reversal_entry_id": reversal.id},
        )

        return reversal

    def search_entries(
        self,
        query: str,
        account_code: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50,
    ) -> list[JournalEntry]:
        """Search journal entries by description text.

        Args:
            query: Search string to match against entry descriptions (case-insensitive)
            account_code: Optional filter by account code
            tag: Optional filter by tag
            limit: Maximum number of entries to return

        Returns:
            List of matching JournalEntry objects
        """
        query_lower = query.lower()
        entries = list(self.data.entries)

        # Text search on description
        entries = [e for e in entries if query_lower in e.description.lower()]

        # Apply additional filters
        if account_code is not None:
            code = account_code.strip().lower()
            entries = [
                e for e in entries
                if any(line.account_code == code for line in e.lines)
            ]

        if tag is not None:
            entries = [e for e in entries if tag in e.tags]

        return sorted(entries, key=lambda e: e.timestamp, reverse=True)[:limit]

    # ── Exchange Rate Management ────────────────────────────────

    def add_exchange_rate(
        self,
        from_currency: str,
        to_currency: str,
        rate: float,
        source: str = "manual",
    ) -> ExchangeRate:
        """Add an exchange rate."""
        exchange_rate = ExchangeRate(
            from_currency=from_currency.upper(),
            to_currency=to_currency.upper(),
            rate=rate,
            source=source,
        )
        self.data.exchange_rates.append(exchange_rate)
        self.audit.log(
            action=AuditAction.EXCHANGE_RATE_ADD,
            details={"from": from_currency, "to": to_currency, "rate": rate},
        )
        self.save()
        return exchange_rate

    def get_currency_converter(self) -> CurrencyConverter:
        """Get a CurrencyConverter populated with this ledger's rates."""
        return CurrencyConverter(self.data.exchange_rates)

    # ── Closed Periods ──────────────────────────────────────────

    def record_closed_period(self, period_data: dict) -> None:
        """Record a closed period in the ledger data."""
        self.data.closed_periods.append(period_data)
        self.save()

    def get_closed_periods(self) -> list[dict]:
        """Get list of closed periods."""
        return list(self.data.closed_periods)
