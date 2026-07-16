"""Custom exceptions for agent-ledger."""


class LedgerError(Exception):
    """Base exception for ledger operations."""
    pass


class AccountNotFoundError(LedgerError):
    """Account does not exist."""
    pass


class AccountAlreadyExistsError(LedgerError):
    """Account already exists."""
    pass


class EntryDoesNotBalanceError(LedgerError):
    """Journal entry debits do not equal credits."""
    pass


class InvalidAccountTypeError(LedgerError):
    """Invalid account type."""
    pass


class InvalidAmountError(LedgerError):
    """Invalid amount for a journal line."""
    pass


class JournalEntryNotFoundError(LedgerError):
    """Journal entry does not exist."""
    pass


class CannotDeleteAccountError(LedgerError):
    """Account has a non-zero balance and cannot be deleted."""
    pass


class CurrencyMismatchError(LedgerError):
    """Currency mismatch between accounts in a journal entry."""
    pass


class ExchangeRateNotFoundError(LedgerError):
    """Exchange rate not found for currency conversion."""
    pass


class ReconciliationError(LedgerError):
    """Error during reconciliation."""
    pass


class LedgerNotInitializedError(LedgerError):
    """Ledger has not been initialized."""
    pass


class PeriodCloseError(LedgerError):
    """Error during period close."""
    pass


class AccountHasChildrenError(LedgerError):
    """Account has child accounts and cannot be deleted."""
    pass


class BankStatementNotFoundError(LedgerError):
    """Bank statement not found."""
    pass


class ReconciliationItemNotFoundError(LedgerError):
    """Reconciliation item not found."""
    pass


class InvalidReconciliationStateError(LedgerError):
    """Invalid state transition for reconciliation."""
    pass


# ── Settlement exceptions ──────────────────────────────────────────


class SettlementNotFoundError(LedgerError):
    """Settlement batch not found."""
    pass


class SettlementItemNotFoundError(LedgerError):
    """Settlement item not found within a batch."""
    pass


class InvalidSettlementStateError(LedgerError):
    """Invalid state transition for a settlement batch."""
    pass


class DuplicateSettlementItemError(LedgerError):
    """A settlement item with the same reference already exists in the batch."""
    pass
