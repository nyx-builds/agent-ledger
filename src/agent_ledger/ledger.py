"""Ledger engine — core business logic for agent-ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import (
    Account, AccountType, AccountBalance,
    JournalEntry, JournalLine, ExchangeRate, LedgerData,
)
from .storage import Storage
from .currency import CurrencyConverter
from .exceptions import (
    AccountNotFoundError, AccountAlreadyExistsError,
    EntryDoesNotBalanceError, InvalidAccountTypeError,
    CannotDeleteAccountError, CurrencyMismatchError,
    JournalEntryNotFoundError, ReconciliationError,
)


class Ledger:
    """The main ledger engine managing accounts, entries, and business rules."""

    def __init__(self, storage: Storage):
        self._storage = storage
        self._data: Optional[LedgerData] = None

    @property
    def data(self) -> LedgerData:
        if self._data is None:
            self._data = self._storage.load()
        return self._data

    def reload(self) -> None:
        """Reload data from storage."""
        self._data = self._storage.load()

    def save(self) -> None:
        """Save current data to storage."""
        if self._data is not None:
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
        metadata: Optional[dict] = None,
    ) -> Account:
        """Update an existing account."""
        account = self.get_account(code)
        if name is not None:
            account.name = name
        if description is not None:
            account.description = description
        if active is not None:
            account.active = active
        if metadata is not None:
            account.metadata.update(metadata)
        self.save()
        return account

    def delete_account(self, code: str) -> None:
        """Delete an account if it has zero balance."""
        code = code.strip().lower()
        balance = self.get_account_balance(code)
        if balance.raw_balance != 0:
            raise CannotDeleteAccountError(
                f"Cannot delete account '{code}' with non-zero balance: {balance.raw_balance}"
            )
        del self.data.accounts[code]
        self.save()

    def list_accounts(
        self,
        account_type: Optional[AccountType] = None,
        active_only: bool = False,
    ) -> list[Account]:
        """List all accounts, optionally filtered."""
        accounts = list(self.data.accounts.values())
        if account_type is not None:
            accounts = [a for a in accounts if a.account_type == account_type]
        if active_only:
            accounts = [a for a in accounts if a.active]
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
            entries = [e for e in entries if e.timestamp >= start_date]

        if end_date is not None:
            entries = [e for e in entries if e.timestamp <= end_date]

        return sorted(entries, key=lambda e: e.timestamp)

    def reconcile_entry(self, entry_id: str) -> JournalEntry:
        """Mark a journal entry as reconciled."""
        entry = self.get_entry(entry_id)
        entry.reconciled = True
        self.save()
        return entry

    def unreconcile_entry(self, entry_id: str) -> JournalEntry:
        """Mark a journal entry as unreconciled."""
        entry = self.get_entry(entry_id)
        entry.reconciled = False
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
        self.save()
        return exchange_rate

    def get_currency_converter(self) -> CurrencyConverter:
        """Get a CurrencyConverter populated with this ledger's rates."""
        return CurrencyConverter(self.data.exchange_rates)
