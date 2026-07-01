"""Auto-categorization engine for Solana transactions.

Maps parsed on-chain transactions to appropriate ledger account codes
based on program type, counterparty, memo, and configurable rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .wallet import (
    SolanaTransaction, ProgramType, TransactionDirection,
    LAMPORTS_PER_SOL,
)


# ── Default Category Mappings ────────────────────────────────────────

DEFAULT_CATEGORIES = {
    # Income sources
    ProgramType.SYSTEM_TRANSFER.value + ":incoming": "sol_income",
    ProgramType.SPL_TOKEN.value + ":incoming": "token_income",
    ProgramType.STAKE.value + ":incoming": "staking_income",
    ProgramType.SWAP.value + ":incoming": "swap_income",
    ProgramType.NFT.value + ":incoming": "nft_income",
    ProgramType.MEMO.value + ":incoming": "other_income",
    ProgramType.COMPUTE_BUDGET.value + ":incoming": "other_income",
    ProgramType.UNKNOWN.value + ":incoming": "other_income",

    # Expense categories
    ProgramType.SYSTEM_TRANSFER.value + ":outgoing": "sol_expense",
    ProgramType.SPL_TOKEN.value + ":outgoing": "token_expense",
    ProgramType.STAKE.value + ":outgoing": "staking_expense",
    ProgramType.SWAP.value + ":outgoing": "swap_expense",
    ProgramType.NFT.value + ":outgoing": "nft_expense",
    ProgramType.MEMO.value + ":outgoing": "other_expense",
    ProgramType.COMPUTE_BUDGET.value + ":outgoing": "compute_fees",
    ProgramType.UNKNOWN.value + ":outgoing": "other_expense",

    "fee": "network_fees",
    "compute_budget": "compute_fees",

    # Self-transfers
    ProgramType.SYSTEM_TRANSFER.value + ":self": "sol_transfers",
    ProgramType.SPL_TOKEN.value + ":self": "token_transfers",
    ProgramType.MEMO.value + ":self": "sol_transfers",
    ProgramType.STAKE.value + ":self": "sol_transfers",
    ProgramType.SWAP.value + ":self": "token_transfers",
    ProgramType.NFT.value + ":self": "sol_transfers",
    ProgramType.COMPUTE_BUDGET.value + ":self": "sol_transfers",
    ProgramType.UNKNOWN.value + ":self": "sol_transfers",
}


# ── Counterparty mappings for well-known addresses ───────────────────

KNOWN_COUNTERPARTIES = {
    # Major DEXes
    "9xKhhTGKKg4Q5EuTex9gHpbZ4QkxE7g6sD1bS8G3YjRT": "raydium",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "raydium",
    "CURVGoZn8eMC4Lqk6jvGMj5sUtC3LY7ZS9UZnP3E1EF": "orca",
    "DjVE6JNiYqPL2QXyCUUh8rNjHrbz9hXHNYt99MQ59x1Q": "orca",
    "whirLbMiicV2nJ25zMA8cJghe6L9KkhGJQJjH5v2qpK": "orca_whirlpool",
    "5quB9WtGcy7FfCLPGg2N7Xz3tN5fQXzbwGMUTvBVjk2Y": "marinade",

    # x402 payment protocol
    "x402": "x402_payments",

    # Wrapping/unwrapping SOL
    "So11111111111111111111111111111111111111112": "wrapped_sol",
    "11111111111111111111111111111111": "system",
}


@dataclass
class CategorizationRule:
    """A rule for categorizing a transaction."""
    name: str
    """Program type to match (optional)."""
    program_type: Optional[ProgramType] = None
    """Direction to match (optional)."""
    direction: Optional[TransactionDirection] = None
    """Counterparty address pattern to match (optional)."""
    counterparty: Optional[str] = None
    """Memo substring to match (optional)."""
    memo_contains: Optional[str] = None
    """Target account code for the credit/debit."""
    account_code: str = ""
    """Priority — higher priority rules override lower ones."""
    priority: int = 0


@dataclass
class CategorizedTransaction:
    """A transaction with its categorization applied."""
    transaction: SolanaTransaction
    """Primary category (e.g., 'sol_income', 'staking_income')."""
    category: str
    """Suggested debit account code."""
    debit_account: str
    """Suggested credit account code."""
    credit_account: str
    """Human-readable description for the journal entry."""
    description: str
    """Amount in the ledger's base currency (SOL as float)."""
    amount: float
    """Fee amount in SOL."""
    fee: float
    """Confidence level of categorization (0.0-1.0)."""
    confidence: float = 1.0
    """Tags to apply to the journal entry."""
    tags: list[str] = field(default_factory=list)


class TransactionCategorizer:
    """Categorizes parsed Solana transactions into ledger account codes."""

    def __init__(
        self,
        categories: Optional[dict[str, str]] = None,
        rules: Optional[list[CategorizationRule]] = None,
        account_prefix: str = "",
    ):
        self.categories = categories if categories is not None else dict(DEFAULT_CATEGORIES)
        self.rules = rules or []
        self.account_prefix = account_prefix

    def categorize(self, tx: SolanaTransaction) -> CategorizedTransaction:
        """Categorize a single transaction."""
        # Check custom rules first (highest priority)
        for rule in sorted(self.rules, key=lambda r: r.priority, reverse=True):
            if self._rule_matches(rule, tx):
                category = rule.account_code
                confidence = 1.0
                break
        else:
            # Use default category mapping
            category = self._default_category(tx)
            confidence = 0.8 if category != "uncategorized" else 0.3

        # Determine debit/credit accounts based on direction
        debit_account, credit_account = self._get_accounts(tx, category)

        # Build description
        description = self._build_description(tx, category)

        # Calculate amounts
        amount = abs(tx.sol_amount_sol)
        fee = tx.fee_sol

        # Build tags
        tags = self._build_tags(tx, category)

        return CategorizedTransaction(
            transaction=tx,
            category=category,
            debit_account=debit_account,
            credit_account=credit_account,
            description=description,
            amount=amount,
            fee=fee,
            confidence=confidence,
            tags=tags,
        )

    def categorize_batch(self, transactions: list[SolanaTransaction]) -> list[CategorizedTransaction]:
        """Categorize a batch of transactions."""
        return [self.categorize(tx) for tx in transactions]

    def _rule_matches(self, rule: CategorizationRule, tx: SolanaTransaction) -> bool:
        """Check if a custom rule matches a transaction."""
        if rule.program_type is not None and tx.program_type != rule.program_type:
            return False
        if rule.direction is not None and tx.direction != rule.direction:
            return False
        if rule.counterparty is not None:
            if not any(rule.counterparty in cp for cp in tx.counterparties):
                return False
        if rule.memo_contains is not None:
            if tx.memo is None or rule.memo_contains.lower() not in tx.memo.lower():
                return False
        return True

    def _default_category(self, tx: SolanaTransaction) -> str:
        """Get the default category for a transaction."""
        # Check program_type + direction first
        direction_key = tx.direction.value
        lookup_key = f"{tx.program_type.value}:{direction_key}"
        if lookup_key in self.categories:
            return self.categories[lookup_key]

        # Check program_type alone
        if tx.program_type.value in self.categories:
            return self.categories[tx.program_type.value]

        # Check counterparty-based categories
        for cp in tx.counterparties:
            if cp in KNOWN_COUNTERPARTIES:
                cp_name = KNOWN_COUNTERPARTIES[cp]
                if cp_name in self.categories:
                    return self.categories[cp_name]

        return "uncategorized"

    def _get_accounts(self, tx: SolanaTransaction, category: str) -> tuple[str, str]:
        """Determine debit and credit account codes for a categorized transaction.

        For an agent's ledger:
        - Incoming SOL: Debit sol_wallet (asset), Credit sol_income (revenue)
        - Outgoing SOL: Debit sol_expense (expense), Credit sol_wallet (asset)
        - Fees: Debit network_fees (expense), Credit sol_wallet (asset)
        - Self-transfers: Debit sol_wallet, Credit sol_wallet (net zero)
        """
        prefix = self.account_prefix

        if tx.direction == TransactionDirection.INCOMING:
            # Money coming in
            debit = f"{prefix}sol_wallet"
            credit = f"{prefix}{category}" if category != "uncategorized" else f"{prefix}other_income"
            return debit, credit

        elif tx.direction == TransactionDirection.OUTGOING:
            # Money going out
            debit = f"{prefix}{category}" if category != "uncategorized" else f"{prefix}other_expense"
            credit = f"{prefix}sol_wallet"
            return debit, credit

        else:  # SELF
            debit = f"{prefix}sol_wallet"
            credit = f"{prefix}sol_wallet"
            return debit, credit

    def _build_description(self, tx: SolanaTransaction, category: str) -> str:
        """Build a human-readable description for the journal entry."""
        parts: list[str] = []

        # Program type label
        program_labels = {
            ProgramType.SYSTEM_TRANSFER: "SOL Transfer",
            ProgramType.SPL_TOKEN: "Token Transfer",
            ProgramType.STAKE: "Stake Operation",
            ProgramType.SWAP: "Token Swap",
            ProgramType.NFT: "NFT Transaction",
            ProgramType.MEMO: "Memo Transaction",
            ProgramType.COMPUTE_BUDGET: "Compute Budget",
            ProgramType.UNKNOWN: "Unknown Transaction",
        }
        parts.append(program_labels.get(tx.program_type, "Transaction"))

        # Direction
        if tx.direction == TransactionDirection.INCOMING:
            parts.append("(incoming)")
        elif tx.direction == TransactionDirection.OUTGOING:
            parts.append("(outgoing)")

        # Amount
        if tx.sol_amount != 0:
            parts.append(f"{abs(tx.sol_amount_sol):.6f} SOL")

        # Counterparty
        if tx.counterparties:
            # Use known name if available, otherwise truncate address
            cp = tx.counterparties[0]
            cp_name = KNOWN_COUNTERPARTIES.get(cp, f"{cp[:8]}...{cp[-4:]}")
            parts.append(f"→ {cp_name}")

        # Memo
        if tx.memo:
            parts.append(f'"{tx.memo[:50]}"')

        return " ".join(parts)

    def _build_tags(self, tx: SolanaTransaction, category: str) -> list[str]:
        """Build tags for the journal entry."""
        tags = [
            "solana",
            tx.program_type.value,
            tx.direction.value,
            category,
        ]
        if tx.counterparties:
            for cp in tx.counterparties:
                cp_name = KNOWN_COUNTERPARTIES.get(cp)
                if cp_name:
                    tags.append(cp_name)
        if tx.memo:
            tags.append("has_memo")
        # Remove duplicates while preserving order
        seen = set()
        unique_tags = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                unique_tags.append(t)
        return unique_tags
