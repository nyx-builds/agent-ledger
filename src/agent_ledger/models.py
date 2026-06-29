"""Core data models for agent-ledger."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AccountType(str, Enum):
    """Standard account types following double-entry accounting conventions."""
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"

    @property
    def normal_balance(self) -> str:
        """Return the normal balance side for this account type.

        Assets and Expenses have normal debit balances.
        Liabilities, Equity, and Revenue have normal credit balances.
        """
        if self in (AccountType.ASSET, AccountType.EXPENSE):
            return "debit"
        return "credit"

    @property
    def is_debit_account(self) -> bool:
        """True if this account type has a normal debit balance."""
        return self.normal_balance == "debit"

    @property
    def is_credit_account(self) -> bool:
        """True if this account type has a normal credit balance."""
        return self.normal_balance == "credit"

    @property
    def is_permanent(self) -> bool:
        """True if this is a permanent (balance sheet) account."""
        return self in (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)

    @property
    def is_temporary(self) -> bool:
        """True if this is a temporary (income statement) account."""
        return self in (AccountType.REVENUE, AccountType.EXPENSE)


class Account(BaseModel):
    """A ledger account in the chart of accounts."""
    code: str = Field(description="Account code (e.g., '1000', 'cash')")
    name: str = Field(description="Human-readable account name")
    account_type: AccountType = Field(description="Type of account")
    currency: str = Field(default="USD", description="ISO 4217 currency code")
    description: str = Field(default="", description="Account description")
    active: bool = Field(default=True, description="Whether the account is active")
    parent_code: Optional[str] = Field(default=None, description="Parent account code for hierarchy")
    tags: list[str] = Field(default_factory=list, description="Tags for filtering and grouping")
    metadata: dict = Field(default_factory=dict, description="Additional metadata")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("code")
    @classmethod
    def code_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Account code must not be empty")
        return v.strip().lower()

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Account name must not be empty")
        return v.strip()

    @field_validator("currency")
    @classmethod
    def currency_must_be_valid(cls, v: str) -> str:
        if len(v) != 3:
            raise ValueError("Currency must be a 3-letter ISO 4217 code")
        return v.upper()


class JournalLine(BaseModel):
    """A single line in a journal entry (one side of a transaction)."""
    account_code: str = Field(description="Account code this line posts to")
    debit: float = Field(default=0.0, ge=0, description="Debit amount (>= 0)")
    credit: float = Field(default=0.0, ge=0, description="Credit amount (>= 0)")
    description: str = Field(default="", description="Line description")

    @field_validator("debit", "credit")
    @classmethod
    def amounts_must_be_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Amounts must be non-negative")
        return round(v, 2)

    @field_validator("account_code")
    @classmethod
    def account_code_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Account code must not be empty")
        return v.strip().lower()

    @property
    def amount(self) -> float:
        """The absolute amount of this line."""
        return max(self.debit, self.credit)

    @property
    def is_debit(self) -> bool:
        """True if this is a debit line."""
        return self.debit > 0

    @property
    def is_credit(self) -> bool:
        """True if this is a credit line."""
        return self.credit > 0

    @property
    def side(self) -> str:
        """Return 'debit' or 'credit'."""
        if self.is_debit:
            return "debit"
        return "credit"


class JournalEntry(BaseModel):
    """A journal entry with multiple lines that must balance."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str = Field(description="Entry description")
    lines: list[JournalLine] = Field(min_length=2, description="At least 2 lines per entry")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    reconciled: bool = Field(default=False, description="Whether this entry is reconciled")
    metadata: dict = Field(default_factory=dict, description="Additional metadata")

    @field_validator("lines")
    @classmethod
    def no_zero_lines(cls, v: list[JournalLine]) -> list[JournalLine]:
        for line in v:
            if line.debit == 0 and line.credit == 0:
                raise ValueError("Journal lines must have a non-zero debit or credit")
        return v

    @field_validator("lines")
    @classmethod
    def no_both_sides(cls, v: list[JournalLine]) -> list[JournalLine]:
        for line in v:
            if line.debit > 0 and line.credit > 0:
                raise ValueError("A journal line cannot have both debit and credit")
        return v

    @field_validator("lines")
    @classmethod
    def lines_must_balance(cls, v: list[JournalLine]) -> list[JournalLine]:
        total_debits = sum(line.debit for line in v)
        total_credits = sum(line.credit for line in v)
        if abs(total_debits - total_credits) > 0.01:
            raise ValueError(
                f"Journal entry does not balance: "
                f"debits={total_debits:.2f}, credits={total_credits:.2f}, "
                f"difference={abs(total_debits - total_credits):.2f}"
            )
        return v

    @property
    def total_debits(self) -> float:
        return round(sum(line.debit for line in self.lines), 2)

    @property
    def total_credits(self) -> float:
        return round(sum(line.credit for line in self.lines), 2)


class ExchangeRate(BaseModel):
    """An exchange rate between two currencies."""
    from_currency: str = Field(description="Source currency code")
    to_currency: str = Field(description="Target currency code")
    rate: float = Field(gt=0, description="Exchange rate (1 from_currency = rate to_currency)")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(default="manual", description="Source of the rate")


class AccountBalance(BaseModel):
    """The balance of an account."""
    account_code: str
    account_name: str
    account_type: AccountType
    currency: str
    debit_total: float = 0.0
    credit_total: float = 0.0

    @property
    def raw_balance(self) -> float:
        """Raw balance (debits - credits). Positive for debit-balance accounts."""
        return round(self.debit_total - self.credit_total, 2)

    @property
    def balance(self) -> float:
        """Balance in the account's normal direction. Always positive or zero for a 'healthy' account."""
        if self.account_type.is_debit_account:
            return round(self.debit_total - self.credit_total, 2)
        return round(self.credit_total - self.debit_total, 2)


class AuditLogData(BaseModel):
    """Audit log data for persistence."""
    entries: list[dict] = Field(default_factory=list, description="Audit log entries as dicts")


class LedgerData(BaseModel):
    """Top-level data structure for persistence."""
    name: str = "Default Ledger"
    base_currency: str = "USD"
    accounts: dict[str, Account] = Field(default_factory=dict)
    entries: list[JournalEntry] = Field(default_factory=list)
    exchange_rates: list[ExchangeRate] = Field(default_factory=list)
    audit_log: AuditLogData = Field(default_factory=AuditLogData, description="Audit log entries")
    closed_periods: list[dict] = Field(default_factory=list, description="List of closed period records")
    bank_statements: list[dict] = Field(default_factory=list, description="Bank statement data for reconciliation")
    budgets: list[dict] = Field(default_factory=list, description="Budget definitions and tracking")
    metadata: dict = Field(default_factory=dict, description="Extensible metadata storage")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
