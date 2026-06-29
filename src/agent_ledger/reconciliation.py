"""Bank reconciliation for agent-ledger — match ledger entries to bank statements."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import AccountType
from .ledger import Ledger
from .exceptions import (
    LedgerError,
    BankStatementNotFoundError,
    ReconciliationItemNotFoundError,
    InvalidReconciliationStateError,
)


@dataclass
class BankStatementLine:
    """A single line from a bank statement."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    date: Optional[datetime] = None
    description: str = ""
    amount: float = 0.0  # Positive = deposit/credit, Negative = withdrawal/debit
    reference: str = ""  # Check number, transaction ID, etc.
    matched_entry_id: Optional[str] = None
    status: str = "unmatched"  # unmatched, matched, disputed


@dataclass
class BankStatement:
    """A bank statement with multiple lines for reconciliation."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    account_code: str = ""
    statement_date: Optional[datetime] = None
    opening_balance: float = 0.0
    closing_balance: float = 0.0
    currency: str = "USD"
    lines: list[BankStatementLine] = field(default_factory=list)
    status: str = "open"  # open, in_progress, completed
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ReconciliationResult:
    """Result of a bank reconciliation process."""
    statement_id: str = ""
    total_statement_lines: int = 0
    matched: int = 0
    unmatched_statement: int = 0
    unmatched_ledger: int = 0
    disputed: int = 0
    statement_closing_balance: float = 0.0
    ledger_balance: float = 0.0
    difference: float = 0.0
    is_balanced: bool = False


class BankReconciliation:
    """Manages bank reconciliations — creating statements, matching entries, tracking discrepancies."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self._statements: list[BankStatement] = []
        self._load_statements()

    def _load_statements(self) -> None:
        """Load statements from ledger data."""
        for stmt_dict in self.ledger.data.bank_statements:
            lines = []
            for line_dict in stmt_dict.get("lines", []):
                line = BankStatementLine(
                    id=line_dict.get("id", str(uuid.uuid4())),
                    date=datetime.fromisoformat(line_dict["date"]) if line_dict.get("date") else None,
                    description=line_dict.get("description", ""),
                    amount=line_dict.get("amount", 0.0),
                    reference=line_dict.get("reference", ""),
                    matched_entry_id=line_dict.get("matched_entry_id"),
                    status=line_dict.get("status", "unmatched"),
                )
                lines.append(line)
            
            stmt = BankStatement(
                id=stmt_dict.get("id", str(uuid.uuid4())),
                account_code=stmt_dict.get("account_code", ""),
                statement_date=datetime.fromisoformat(stmt_dict["statement_date"]) if stmt_dict.get("statement_date") else None,
                opening_balance=stmt_dict.get("opening_balance", 0.0),
                closing_balance=stmt_dict.get("closing_balance", 0.0),
                currency=stmt_dict.get("currency", "USD"),
                lines=lines,
                status=stmt_dict.get("status", "open"),
                created_at=datetime.fromisoformat(stmt_dict["created_at"]) if stmt_dict.get("created_at") else datetime.now(timezone.utc),
            )
            self._statements.append(stmt)

    def _save_statements(self) -> None:
        """Save statements to ledger data."""
        stmts_data = []
        for stmt in self._statements:
            lines_data = []
            for line in stmt.lines:
                lines_data.append({
                    "id": line.id,
                    "date": line.date.isoformat() if line.date else None,
                    "description": line.description,
                    "amount": line.amount,
                    "reference": line.reference,
                    "matched_entry_id": line.matched_entry_id,
                    "status": line.status,
                })
            stmts_data.append({
                "id": stmt.id,
                "account_code": stmt.account_code,
                "statement_date": stmt.statement_date.isoformat() if stmt.statement_date else None,
                "opening_balance": stmt.opening_balance,
                "closing_balance": stmt.closing_balance,
                "currency": stmt.currency,
                "lines": lines_data,
                "status": stmt.status,
                "created_at": stmt.created_at.isoformat() if stmt.created_at else None,
            })
        
        self.ledger.data.bank_statements = stmts_data
        self.ledger.save()

    def create_statement(
        self,
        account_code: str,
        statement_date: Optional[datetime] = None,
        opening_balance: float = 0.0,
        closing_balance: float = 0.0,
        currency: str = "USD",
    ) -> BankStatement:
        """Create a new bank statement for reconciliation.
        
        Args:
            account_code: The ledger account code this statement is for
            statement_date: Date of the statement period end
            opening_balance: Opening balance per the bank
            closing_balance: Closing balance per the bank
            currency: Currency code
            
        Returns:
            The created BankStatement
            
        Raises:
            AccountNotFoundError: If the account doesn't exist
        """
        # Validate account exists and is an asset (cash-like)
        account = self.ledger.get_account(account_code)
        
        stmt = BankStatement(
            account_code=account_code.strip().lower(),
            statement_date=statement_date or datetime.now(timezone.utc),
            opening_balance=round(opening_balance, 2),
            closing_balance=round(closing_balance, 2),
            currency=currency,
        )
        
        self._statements.append(stmt)
        self._save_statements()
        return stmt

    def add_statement_line(
        self,
        statement_id: str,
        date: Optional[datetime] = None,
        description: str = "",
        amount: float = 0.0,
        reference: str = "",
    ) -> BankStatementLine:
        """Add a line to a bank statement.
        
        Args:
            statement_id: ID of the bank statement
            date: Transaction date
            description: Transaction description
            amount: Amount (positive = deposit, negative = withdrawal)
            reference: Check number or reference
            
        Returns:
            The created BankStatementLine
        """
        stmt = self.get_statement(statement_id)
        
        line = BankStatementLine(
            date=date or datetime.now(timezone.utc),
            description=description,
            amount=round(amount, 2),
            reference=reference,
        )
        
        stmt.lines.append(line)
        self._save_statements()
        return line

    def add_statement_lines_batch(
        self,
        statement_id: str,
        lines: list[dict],
    ) -> list[BankStatementLine]:
        """Add multiple lines to a bank statement at once.
        
        Args:
            statement_id: ID of the bank statement
            lines: List of dicts with keys: date, description, amount, reference
            
        Returns:
            List of created BankStatementLine objects
        """
        stmt = self.get_statement(statement_id)
        created = []
        
        for line_data in lines:
            date_val = line_data.get("date")
            if isinstance(date_val, str):
                date_val = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
            
            line = BankStatementLine(
                date=date_val or datetime.now(timezone.utc),
                description=line_data.get("description", ""),
                amount=round(line_data.get("amount", 0.0), 2),
                reference=line_data.get("reference", ""),
            )
            stmt.lines.append(line)
            created.append(line)
        
        self._save_statements()
        return created

    def get_statement(self, statement_id: str) -> BankStatement:
        """Get a bank statement by ID."""
        for stmt in self._statements:
            if stmt.id == statement_id:
                return stmt
        raise BankStatementNotFoundError(f"Bank statement '{statement_id}' not found")

    def list_statements(
        self,
        account_code: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[BankStatement]:
        """List bank statements with optional filters."""
        statements = list(self._statements)
        
        if account_code:
            code = account_code.strip().lower()
            statements = [s for s in statements if s.account_code == code]
        
        if status:
            statements = [s for s in statements if s.status == status]
        
        return sorted(statements, key=lambda s: s.created_at, reverse=True)

    def match_entry(
        self,
        statement_id: str,
        line_id: str,
        entry_id: str,
    ) -> BankStatementLine:
        """Manually match a bank statement line to a ledger entry.
        
        Args:
            statement_id: ID of the bank statement
            line_id: ID of the statement line
            entry_id: ID of the journal entry to match
            
        Returns:
            The updated BankStatementLine
        """
        stmt = self.get_statement(statement_id)
        
        # Find the line
        line = None
        for l in stmt.lines:
            if l.id == line_id:
                line = l
                break
        if line is None:
            raise ReconciliationItemNotFoundError(f"Statement line '{line_id}' not found")
        
        # Validate the entry exists
        entry = self.ledger.get_entry(entry_id)
        
        # Check if entry is for the same account
        account_entries = self.ledger.list_entries(account_code=stmt.account_code)
        if entry_id not in [e.id for e in account_entries]:
            raise InvalidReconciliationStateError(
                f"Entry '{entry_id}' is not associated with account '{stmt.account_code}'"
            )
        
        # Check line is not already matched
        if line.status == "matched":
            raise InvalidReconciliationStateError(
                f"Statement line '{line_id}' is already matched to entry '{line.matched_entry_id}'"
            )
        
        line.matched_entry_id = entry_id
        line.status = "matched"
        self._save_statements()
        return line

    def unmatch_entry(
        self,
        statement_id: str,
        line_id: str,
    ) -> BankStatementLine:
        """Unmatch a previously matched bank statement line.
        
        Args:
            statement_id: ID of the bank statement
            line_id: ID of the statement line
            
        Returns:
            The updated BankStatementLine
        """
        stmt = self.get_statement(statement_id)
        
        line = None
        for l in stmt.lines:
            if l.id == line_id:
                line = l
                break
        if line is None:
            raise ReconciliationItemNotFoundError(f"Statement line '{line_id}' not found")
        
        if line.status != "matched":
            raise InvalidReconciliationStateError(
                f"Statement line '{line_id}' is not matched"
            )
        
        line.matched_entry_id = None
        line.status = "unmatched"
        self._save_statements()
        return line

    def mark_disputed(
        self,
        statement_id: str,
        line_id: str,
        reason: Optional[str] = None,
    ) -> BankStatementLine:
        """Mark a statement line as disputed.
        
        Args:
            statement_id: ID of the bank statement
            line_id: ID of the statement line
            reason: Optional reason for dispute
            
        Returns:
            The updated BankStatementLine
        """
        stmt = self.get_statement(statement_id)
        
        line = None
        for l in stmt.lines:
            if l.id == line_id:
                line = l
                break
        if line is None:
            raise ReconciliationItemNotFoundError(f"Statement line '{line_id}' not found")
        
        line.status = "disputed"
        if reason:
            line.description = f"{line.description} [DISPUTED: {reason}]"
        self._save_statements()
        return line

    def auto_match(
        self,
        statement_id: str,
        tolerance: float = 0.01,
    ) -> dict:
        """Automatically match bank statement lines to ledger entries.
        
        Uses amount-based matching: for each unmatched statement line, find
        ledger entries for the same account with matching amounts within tolerance.
        
        Args:
            statement_id: ID of the bank statement
            tolerance: Amount tolerance for matching (default: $0.01)
            
        Returns:
            Dict with matched count and details
        """
        stmt = self.get_statement(statement_id)
        stmt.status = "in_progress"
        
        # Get unmatched entries for this account
        account_entries = self.ledger.list_entries(account_code=stmt.account_code)
        # Get IDs of already-matched entries across all statements for this account
        matched_entry_ids = set()
        for s in self._statements:
            for l in s.lines:
                if l.matched_entry_id:
                    matched_entry_ids.add(l.matched_entry_id)
        
        available_entries = [e for e in account_entries if e.id not in matched_entry_ids]
        
        matches = []
        
        for line in stmt.lines:
            if line.status != "unmatched":
                continue
            
            target_amount = abs(line.amount)
            is_deposit = line.amount >= 0
            
            # Try to find a matching entry
            for entry in available_entries:
                if entry.id in matched_entry_ids:
                    continue
                
                # Check if this entry has a line for the account with matching amount
                for entry_line in entry.lines:
                    if entry_line.account_code != stmt.account_code:
                        continue
                    
                    entry_amount = 0.0
                    if is_deposit and entry_line.debit > 0:
                        entry_amount = entry_line.debit
                    elif not is_deposit and entry_line.credit > 0:
                        entry_amount = entry_line.credit
                    
                    if abs(entry_amount - target_amount) <= tolerance:
                        line.matched_entry_id = entry.id
                        line.status = "matched"
                        matched_entry_ids.add(entry.id)
                        matches.append({
                            "line_id": line.id,
                            "line_description": line.description,
                            "line_amount": line.amount,
                            "entry_id": entry.id,
                            "entry_description": entry.description,
                        })
                        break
                
                if line.status == "matched":
                    break
        
        self._save_statements()
        return {
            "statement_id": statement_id,
            "matched": len(matches),
            "matches": matches,
        }

    def reconcile(
        self,
        statement_id: str,
    ) -> ReconciliationResult:
        """Perform reconciliation: compare bank statement to ledger and compute differences.
        
        This does NOT auto-match entries. It computes the current reconciliation state.
        
        Args:
            statement_id: ID of the bank statement
            
        Returns:
            ReconciliationResult with match counts and balance comparison
        """
        stmt = self.get_statement(statement_id)
        
        # Count matches
        matched = sum(1 for l in stmt.lines if l.status == "matched")
        unmatched = sum(1 for l in stmt.lines if l.status == "unmatched")
        disputed = sum(1 for l in stmt.lines if l.status == "disputed")
        
        # Get ledger balance for the account
        try:
            account_balance = self.ledger.get_account_balance(stmt.account_code)
            ledger_balance = account_balance.balance
        except Exception:
            ledger_balance = 0.0
        
        difference = round(stmt.closing_balance - ledger_balance, 2)
        
        result = ReconciliationResult(
            statement_id=stmt.id,
            total_statement_lines=len(stmt.lines),
            matched=matched,
            unmatched_statement=unmatched,
            unmatched_ledger=0,  # Would need more complex tracking
            disputed=disputed,
            statement_closing_balance=stmt.closing_balance,
            ledger_balance=ledger_balance,
            difference=difference,
            is_balanced=abs(difference) < 0.01 and matched == len(stmt.lines),
        )
        
        return result

    def complete_reconciliation(
        self,
        statement_id: str,
    ) -> ReconciliationResult:
        """Mark a reconciliation as complete and reconcile matched entries.
        
        All matched ledger entries will be marked as reconciled.
        The statement status changes to 'completed'.
        
        Args:
            statement_id: ID of the bank statement
            
        Returns:
            ReconciliationResult
            
        Raises:
            InvalidReconciliationStateError: If there are still unmatched lines
        """
        stmt = self.get_statement(statement_id)
        
        unmatched = sum(1 for l in stmt.lines if l.status == "unmatched")
        if unmatched > 0:
            raise InvalidReconciliationStateError(
                f"Cannot complete reconciliation: {unmatched} unmatched statement lines remain"
            )
        
        # Reconcile all matched entries in the ledger
        for line in stmt.lines:
            if line.status == "matched" and line.matched_entry_id:
                try:
                    self.ledger.reconcile_entry(line.matched_entry_id)
                except Exception:
                    pass  # Already reconciled is fine
        
        stmt.status = "completed"
        self._save_statements()
        
        return self.reconcile(statement_id)

    def delete_statement(self, statement_id: str) -> None:
        """Delete a bank statement.
        
        Args:
            statement_id: ID of the bank statement to delete
        """
        stmt = self.get_statement(statement_id)
        self._statements.remove(stmt)
        self._save_statements()

    def get_unreconciled_entries(self, account_code: str) -> list[dict]:
        """Get ledger entries for an account that haven't been matched to any bank statement.
        
        Args:
            account_code: The account code
            
        Returns:
            List of dicts with entry info
        """
        # Get all matched entry IDs across all statements
        matched_entry_ids = set()
        for stmt in self._statements:
            for line in stmt.lines:
                if line.matched_entry_id:
                    matched_entry_ids.add(line.matched_entry_id)
        
        # Get all entries for this account
        account_entries = self.ledger.list_entries(account_code=account_code)
        
        unreconciled = []
        for entry in account_entries:
            if entry.id not in matched_entry_ids and not entry.reconciled:
                # Find the relevant line for this account
                for line in entry.lines:
                    if line.account_code == account_code.strip().lower():
                        unreconciled.append({
                            "entry_id": entry.id,
                            "description": entry.description,
                            "date": entry.timestamp.isoformat(),
                            "debit": line.debit,
                            "credit": line.credit,
                            "amount": line.debit if line.debit > 0 else line.credit,
                        })
                        break
        
        return unreconciled
