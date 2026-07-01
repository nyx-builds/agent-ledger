"""Wallet-to-ledger importer — sync on-chain transactions into journal entries.

Fetches transactions from a Solana wallet, categorizes them, and creates
corresponding journal entries in the ledger. Tracks which transactions
have already been imported to avoid duplicates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .wallet import (
    SolanaWalletClient, SolanaTransaction, WalletInfo, WalletError,
    SOLANA_MAINNET_RPC, SOLANA_DEVNET_RPC,
)
from .categorizer import TransactionCategorizer, CategorizedTransaction
from .ledger import Ledger
from .models import AccountType, JournalLine
from .storage import Storage
from .exceptions import LedgerError


# ── Sync State ───────────────────────────────────────────────────────

SYNC_STATE_DIR = Path.home() / ".agent-ledger" / "wallet-sync"


@dataclass
class WalletSyncState:
    """Tracks the sync state for a wallet."""
    wallet_address: str
    network: str
    last_synced_signature: Optional[str] = None
    last_synced_slot: Optional[int] = None
    last_synced_at: Optional[datetime] = None
    total_imported: int = 0
    imported_signatures: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "wallet_address": self.wallet_address,
            "network": self.network,
            "last_synced_signature": self.last_synced_signature,
            "last_synced_slot": self.last_synced_slot,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "total_imported": self.total_imported,
            "imported_signatures": sorted(self.imported_signatures),
        }

    @classmethod
    def from_dict(cls, data: dict) -> WalletSyncState:
        imported_sigs = data.get("imported_signatures", [])
        last_synced_at = data.get("last_synced_at")
        return cls(
            wallet_address=data["wallet_address"],
            network=data["network"],
            last_synced_signature=data.get("last_synced_signature"),
            last_synced_slot=data.get("last_synced_slot"),
            last_synced_at=datetime.fromisoformat(last_synced_at) if last_synced_at else None,
            total_imported=data.get("total_imported", 0),
            imported_signatures=set(imported_sigs),
        )


@dataclass
class ImportResult:
    """Result of a wallet sync operation."""
    wallet_address: str
    transactions_fetched: int = 0
    transactions_imported: int = 0
    transactions_skipped: int = 0  # Already imported
    transactions_failed: int = 0
    total_sol_imported: float = 0.0
    entries_created: list[str] = field(default_factory=list)  # Entry IDs
    errors: list[str] = field(default_factory=list)


class WalletImporter:
    """Imports Solana wallet transactions into the ledger."""

    def __init__(
        self,
        ledger: Ledger,
        categorizer: Optional[TransactionCategorizer] = None,
        rpc_url: str = SOLANA_MAINNET_RPC,
        sync_dir: Optional[Path] = None,
    ):
        self.ledger = ledger
        self.categorizer = categorizer or TransactionCategorizer()
        self.rpc_url = rpc_url
        self.sync_dir = sync_dir or SYNC_STATE_DIR
        self._client: Optional[SolanaWalletClient] = None
        self._sync_states: dict[str, WalletSyncState] = {}

    @property
    def client(self) -> SolanaWalletClient:
        if self._client is None:
            self._client = SolanaWalletClient(rpc_url=self.rpc_url)
        return self._client

    def close(self):
        if self._client:
            self._client.close()

    # ── Wallet Connection ─────────────────────────────────────────

    def connect_wallet(self, wallet_address: str, network: str = "mainnet") -> WalletInfo:
        """Connect a wallet and get its current state."""
        rpc_url = SOLANA_MAINNET_RPC if network == "mainnet" else SOLANA_DEVNET_RPC
        if self._client:
            self._client.close()
        self._client = SolanaWalletClient(rpc_url=rpc_url)

        wallet_address = wallet_address.strip()
        lamports = self.client.get_balance(wallet_address)

        # Load existing sync state
        state = self._load_sync_state(wallet_address, network)
        self._sync_states[wallet_address] = state

        # Save the wallet address in the ledger metadata
        self._save_wallet_config(wallet_address, network)

        return WalletInfo(
            address=wallet_address,
            network=network,
            lamports=lamports,
            last_synced_slot=state.last_synced_slot,
            last_synced_at=state.last_synced_at,
            transaction_count=state.total_imported,
        )

    def get_wallet_info(self, wallet_address: str, network: str = "mainnet") -> WalletInfo:
        """Get current wallet info without connecting."""
        wallet_address = wallet_address.strip()
        rpc_url = SOLANA_MAINNET_RPC if network == "mainnet" else SOLANA_DEVNET_RPC
        client = SolanaWalletClient(rpc_url=rpc_url)
        try:
            lamports = client.get_balance(wallet_address)
        finally:
            client.close()

        state = self._load_sync_state(wallet_address, network)

        return WalletInfo(
            address=wallet_address,
            network=network,
            lamports=lamports,
            last_synced_slot=state.last_synced_slot,
            last_synced_at=state.last_synced_at,
            transaction_count=state.total_imported,
        )

    # ── Default Account Setup ─────────────────────────────────────

    def setup_wallet_accounts(self) -> list[str]:
        """Create the default chart of accounts for a Solana wallet.

        Returns list of created account codes.
        """
        created = []

        # Asset accounts
        asset_accounts = [
            ("sol_wallet", "SOL Wallet", AccountType.ASSET),
            ("token_wallet", "Token Wallet", AccountType.ASSET),
        ]

        # Revenue accounts
        revenue_accounts = [
            ("sol_income", "SOL Income", AccountType.REVENUE),
            ("token_income", "Token Income", AccountType.REVENUE),
            ("staking_income", "Staking Income", AccountType.REVENUE),
            ("swap_income", "Swap Income", AccountType.REVENUE),
            ("nft_income", "NFT Income", AccountType.REVENUE),
            ("other_income", "Other Income", AccountType.REVENUE),
        ]

        # Expense accounts
        expense_accounts = [
            ("sol_expense", "SOL Expense", AccountType.EXPENSE),
            ("token_expense", "Token Expense", AccountType.EXPENSE),
            ("staking_expense", "Staking Expense", AccountType.EXPENSE),
            ("swap_expense", "Swap Expense", AccountType.EXPENSE),
            ("nft_expense", "NFT Expense", AccountType.EXPENSE),
            ("network_fees", "Network Fees", AccountType.EXPENSE),
            ("compute_fees", "Compute Fees", AccountType.EXPENSE),
            ("other_expense", "Other Expense", AccountType.EXPENSE),
        ]

        all_accounts = asset_accounts + revenue_accounts + expense_accounts

        for code, name, acct_type in all_accounts:
            try:
                self.ledger.get_account(code)
            except Exception:
                try:
                    self.ledger.create_account(
                        code=code,
                        name=name,
                        account_type=acct_type,
                        currency="SOL",
                    )
                    created.append(code)
                except LedgerError:
                    pass

        return created

    # ── Sync / Import ─────────────────────────────────────────────

    def sync_wallet(
        self,
        wallet_address: str,
        network: str = "mainnet",
        limit: int = 50,
        create_accounts: bool = True,
        import_fees: bool = True,
        dry_run: bool = False,
    ) -> ImportResult:
        """Sync a wallet's on-chain transactions into the ledger.

        Args:
            wallet_address: Solana wallet address
            network: 'mainnet' or 'devnet'
            limit: Max transactions to fetch
            create_accounts: Auto-create missing ledger accounts
            import_fees: Create separate fee entries
            dry_run: If True, categorize but don't post entries

        Returns:
            ImportResult with statistics
        """
        result = ImportResult(wallet_address=wallet_address)

        # Ensure wallet is connected
        rpc_url = SOLANA_MAINNET_RPC if network == "mainnet" else SOLANA_DEVNET_RPC
        if self._client is None or self.rpc_url != rpc_url:
            self.rpc_url = rpc_url
            if self._client:
                self._client.close()
            self._client = SolanaWalletClient(rpc_url=rpc_url)

        # Load sync state
        state = self._load_sync_state(wallet_address, network)

        # Optionally create default accounts
        if create_accounts and not dry_run:
            self.setup_wallet_accounts()

        # Fetch transactions
        try:
            # If we've synced before, use the last signature as 'before' for pagination
            before_sig = state.last_synced_signature if state.last_synced_signature else None
            transactions = self.client.fetch_transactions(
                wallet_address,
                limit=limit,
                before_signature=before_sig,
            )
        except WalletError as e:
            result.errors.append(f"Failed to fetch transactions: {e}")
            return result
        except Exception as e:
            result.errors.append(f"Unexpected error: {e}")
            return result

        result.transactions_fetched = len(transactions)

        # Categorize and import each transaction
        for tx in transactions:
            # Skip already-imported transactions
            if tx.signature in state.imported_signatures:
                result.transactions_skipped += 1
                continue

            # Skip failed transactions
            if tx.status.value == "failed":
                result.transactions_skipped += 1
                continue

            # Categorize
            cat = self.categorizer.categorize(tx)

            if dry_run:
                result.transactions_imported += 1
                continue

            # Post journal entry for the main transaction
            try:
                entry_id = self._post_transaction_entry(cat, import_fees)
                if entry_id:
                    result.entries_created.append(entry_id)
                    result.transactions_imported += 1
                    result.total_sol_imported += cat.amount
                else:
                    result.transactions_failed += 1
            except Exception as e:
                result.transactions_failed += 1
                result.errors.append(f"Failed to import {tx.signature[:16]}...: {e}")

            # Post separate fee entry if requested
            if import_fees and cat.fee > 0 and not dry_run:
                try:
                    fee_id = self._post_fee_entry(cat)
                    if fee_id:
                        result.entries_created.append(fee_id)
                except Exception as e:
                    result.errors.append(f"Failed to import fee for {tx.signature[:16]}...: {e}")

            # Update sync state
            state.imported_signatures.add(tx.signature)

        # Update sync state
        if transactions:
            # Transactions come newest-first, so the last one is the oldest
            oldest = transactions[-1]
            state.last_synced_signature = oldest.signature
            state.last_synced_slot = oldest.slot
            state.last_synced_at = datetime.now(timezone.utc)
            state.total_imported += result.transactions_imported

        self._save_sync_state(state)
        self._sync_states[wallet_address] = state

        return result

    # ── Private Helpers ───────────────────────────────────────────

    def _post_transaction_entry(
        self,
        cat: CategorizedTransaction,
        import_fees: bool = True,
    ) -> Optional[str]:
        """Post a journal entry for a categorized transaction.

        Returns the entry ID or None if the entry was zero-amount.
        """
        amount = cat.amount
        if amount <= 0 and cat.transaction.direction.value != "self":
            return None

        # Ensure accounts exist
        self._ensure_account(cat.debit_account)
        self._ensure_account(cat.credit_account)

        # For self-transfers, skip (net zero)
        if cat.transaction.direction.value == "self" and amount == 0:
            return None

        # Build journal lines
        lines = []
        debit_line = JournalLine(
            account_code=cat.debit_account,
            debit=round(amount, 9),
            credit=0.0,
        )
        credit_line = JournalLine(
            account_code=cat.credit_account,
            debit=0.0,
            credit=round(amount, 9),
        )
        lines = [debit_line, credit_line]

        # Include fee in the same entry if not importing separately
        if not import_fees and cat.fee > 0:
            # Add fee as an extra debit
            fee_debit = JournalLine(
                account_code="network_fees",
                debit=round(cat.fee, 9),
                credit=0.0,
            )
            # Adjust the credit to include the fee
            total_credit = round(amount + cat.fee, 9)
            credit_line = JournalLine(
                account_code=cat.credit_account,
                debit=0.0,
                credit=total_credit,
            )
            lines = [debit_line, fee_debit, credit_line]

        # Create the journal entry
        tx = cat.transaction
        entry = self.ledger.post_entry(
            description=cat.description,
            lines=lines,
            tags=cat.tags,
            timestamp=tx.timestamp or datetime.now(timezone.utc),
            metadata={
                "source": "solana_wallet",
                "wallet_address": cat.transaction.signature and wallet_address_from_tx(tx) or "",
                "signature": tx.signature,
                "slot": tx.slot,
                "program_type": tx.program_type.value,
                "direction": tx.direction.value,
                "category": cat.category,
                "confidence": cat.confidence,
                "sol_amount_lamports": tx.sol_amount,
                "fee_lamports": tx.fee_lamports,
            },
        )

        return entry.id

    def _post_fee_entry(self, cat: CategorizedTransaction) -> Optional[str]:
        """Post a separate journal entry for the transaction fee."""
        # Skip if fee rounds to zero at 2 decimal places (journal precision)
        if cat.fee <= 0 or round(cat.fee, 2) <= 0:
            return None

        # Ensure fee accounts exist
        self._ensure_account("network_fees")
        self._ensure_account("sol_wallet")

        lines = [
            JournalLine(account_code="network_fees", debit=round(cat.fee, 9), credit=0.0),
            JournalLine(account_code="sol_wallet", debit=0.0, credit=round(cat.fee, 9)),
        ]

        entry = self.ledger.post_entry(
            description=f"Network Fee: {cat.description}",
            lines=lines,
            tags=["solana", "fee", "network_fees"],
            timestamp=cat.transaction.timestamp or datetime.now(timezone.utc),
            metadata={
                "source": "solana_wallet_fee",
                "signature": cat.transaction.signature,
                "fee_lamports": cat.transaction.fee_lamports,
            },
        )

        return entry.id

    def _ensure_account(self, code: str) -> None:
        """Create an account if it doesn't exist."""
        try:
            self.ledger.get_account(code)
        except Exception:
            # Determine account type from the code
            acct_type = self._infer_account_type(code)
            name = code.replace("_", " ").title()
            try:
                self.ledger.create_account(
                    code=code,
                    name=name,
                    account_type=acct_type,
                    currency="SOL",
                )
            except LedgerError:
                pass

    @staticmethod
    def _infer_account_type(code: str) -> AccountType:
        """Infer account type from the account code."""
        if any(kw in code for kw in ("wallet", "asset", "bank", "cash")):
            return AccountType.ASSET
        if any(kw in code for kw in ("income", "revenue", "earning")):
            return AccountType.REVENUE
        if any(kw in code for kw in ("expense", "fee", "cost")):
            return AccountType.EXPENSE
        if any(kw in code for kw in ("liability", "debt", "payable")):
            return AccountType.LIABILITY
        return AccountType.EXPENSE  # Default for unknowns

    # ── Sync State Persistence ────────────────────────────────────

    def _sync_state_path(self, wallet_address: str, network: str) -> Path:
        """Get the file path for a wallet's sync state."""
        safe_addr = wallet_address.replace("/", "_").replace("\\", "_")
        return self.sync_dir / network / f"{safe_addr}.json"

    def _load_sync_state(self, wallet_address: str, network: str) -> WalletSyncState:
        """Load sync state from disk."""
        path = self._sync_state_path(wallet_address, network)

        if wallet_address in self._sync_states:
            return self._sync_states[wallet_address]

        if path.exists():
            try:
                data = json.loads(path.read_text())
                return WalletSyncState.from_dict(data)
            except Exception:
                pass

        return WalletSyncState(wallet_address=wallet_address, network=network)

    def _save_sync_state(self, state: WalletSyncState) -> None:
        """Save sync state to disk."""
        path = self._sync_state_path(state.wallet_address, state.network)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state.to_dict(), indent=2, default=str))

    # ── Wallet Config ─────────────────────────────────────────────

    def _save_wallet_config(self, wallet_address: str, network: str) -> None:
        """Save wallet connection info in a separate config file."""
        config_path = self.sync_dir / "wallet_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "wallet_address": wallet_address,
            "network": network,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }
        config_path.write_text(json.dumps(config, indent=2))

    def load_wallet_config(self) -> Optional[dict]:
        """Load the wallet connection config if it exists."""
        config_path = self.sync_dir / "wallet_config.json"
        if config_path.exists():
            try:
                return json.loads(config_path.read_text())
            except Exception:
                return None
        return None


def wallet_address_from_tx(tx: SolanaTransaction) -> str:
    """Extract wallet address from transaction metadata if available."""
    if tx.raw_data and isinstance(tx.raw_data, dict):
        return tx.raw_data.get("wallet_address", "")
    return ""
