"""Solana wallet integration — fetch and parse on-chain transactions.

Uses the Solana JSON RPC API to retrieve transaction history for a wallet
address, parse the instructions, and extract meaningful financial data
(amounts, counterparties, program types).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import httpx


# ── Constants ────────────────────────────────────────────────────────

SOLANA_MAINNET_RPC = "https://api.mainnet-beta.solana.com"
SOLANA_DEVNET_RPC = "https://api.devnet.solana.com"

LAMPORTS_PER_SOL = 1_000_000_000

# Well-known program IDs
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
STAKE_PROGRAM_ID = "Stake11111111111111111111111111111111111111"
MEMO_PROGRAM_ID = "MemoSq4gqABaxKbGx2V8ZQpRCbfZfjC8SF1TAxzHgsP"
COMPUTE_BUDGET_PROGRAM_ID = "ComputeBudget111111111111111111111111111111"


class ProgramType(str, Enum):
    """Classification of Solana program interactions."""
    SYSTEM_TRANSFER = "system_transfer"
    SPL_TOKEN = "spl_token"
    STAKE = "stake"
    SWAP = "swap"
    NFT = "nft"
    MEMO = "memo"
    COMPUTE_BUDGET = "compute_budget"
    UNKNOWN = "unknown"


class TransactionDirection(str, Enum):
    """Whether a transaction is incoming or outgoing from the wallet's perspective."""
    INCOMING = "incoming"
    OUTGOING = "outgoing"
    SELF = "self"


class TransactionStatus(str, Enum):
    """On-chain transaction status."""
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class SolanaTransaction:
    """Parsed Solana transaction relevant to a wallet."""
    signature: str
    slot: int
    block_time: Optional[int]
    status: TransactionStatus
    direction: TransactionDirection
    program_type: ProgramType
    fee_lamports: int
    sol_amount: int  # lamports (positive = incoming, negative = outgoing)
    token_transfers: list[dict] = field(default_factory=list)
    counterparties: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    memo: Optional[str] = None
    raw_data: Optional[dict] = None

    @property
    def sol_amount_sol(self) -> float:
        """SOL amount as a decimal."""
        return self.sol_amount / LAMPORTS_PER_SOL

    @property
    def fee_sol(self) -> float:
        """Fee in SOL."""
        return self.fee_lamports / LAMPORTS_PER_SOL

    @property
    def timestamp(self) -> Optional[datetime]:
        """Block time as a datetime."""
        if self.block_time is None:
            return None
        return datetime.fromtimestamp(self.block_time, tz=timezone.utc)

    @property
    def is_income(self) -> bool:
        """Whether this transaction represents income for the wallet."""
        return self.direction == TransactionDirection.INCOMING and self.sol_amount > 0

    @property
    def is_expense(self) -> bool:
        """Whether this transaction represents an expense."""
        return self.direction == TransactionDirection.OUTGOING


@dataclass
class WalletInfo:
    """Summary info about a connected wallet."""
    address: str
    network: str
    lamports: int = 0
    last_synced_slot: Optional[int] = None
    last_synced_at: Optional[datetime] = None
    transaction_count: int = 0

    @property
    def sol_balance(self) -> float:
        return self.lamports / LAMPORTS_PER_SOL


class SolanaWalletClient:
    """Client for fetching and parsing Solana wallet transactions."""

    def __init__(
        self,
        rpc_url: str = SOLANA_MAINNET_RPC,
        http_timeout: float = 30.0,
    ):
        self.rpc_url = rpc_url
        self.http_timeout = http_timeout
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=self.http_timeout)
        return self._client

    def close(self):
        if self._client and not self._client.is_closed:
            self._client.close()

    def _rpc_call(self, method: str, params: list) -> dict | list:
        """Make a JSON RPC call to the Solana RPC endpoint."""
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method,
            "params": params,
        }
        response = self.client.post(self.rpc_url, json=payload)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise WalletError(f"RPC error: {data['error'].get('message', data['error'])}")
        return data.get("result", {})

    def get_balance(self, address: str) -> int:
        """Get SOL balance in lamports."""
        result = self._rpc_call("getBalance", [address])
        if isinstance(result, dict):
            return result.get("value", 0)
        return 0

    def get_signatures_for_address(
        self,
        address: str,
        limit: int = 100,
        before: Optional[str] = None,
        until: Optional[str] = None,
    ) -> list[dict]:
        """Get transaction signatures for a wallet address."""
        config: dict = {"limit": limit, "commitment": "finalized"}
        if before:
            config["before"] = before
        if until:
            config["until"] = until
        result = self._rpc_call("getSignaturesForAddress", [address, config])
        if isinstance(result, list):
            return result
        return []

    def get_transaction(self, signature: str) -> Optional[dict]:
        """Get full transaction details by signature."""
        result = self._rpc_call(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        if isinstance(result, dict):
            return result
        return None

    def fetch_transactions(
        self,
        wallet_address: str,
        limit: int = 50,
        before_signature: Optional[str] = None,
    ) -> list[SolanaTransaction]:
        """Fetch and parse recent transactions for a wallet."""
        wallet_address = wallet_address.strip()

        # Get signatures
        sig_infos = self.get_signatures_for_address(
            wallet_address, limit=limit, before=before_signature,
        )
        if not sig_infos:
            return []

        transactions: list[SolanaTransaction] = []
        for sig_info in sig_infos:
            signature = sig_info["signature"]
            err = sig_info.get("err")
            slot = sig_info.get("slot", 0)
            block_time = sig_info.get("blockTime")

            # Fetch full transaction
            try:
                tx_data = self.get_transaction(signature)
            except Exception:
                tx_data = None

            if tx_data is None:
                transactions.append(SolanaTransaction(
                    signature=signature,
                    slot=slot,
                    block_time=block_time,
                    status=TransactionStatus.FAILED if err else TransactionStatus.SUCCESS,
                    direction=TransactionDirection.SELF,
                    program_type=ProgramType.UNKNOWN,
                    fee_lamports=0,
                    sol_amount=0,
                ))
                continue

            # Parse the transaction
            parsed = self._parse_transaction(tx_data, wallet_address)
            parsed.signature = signature
            parsed.slot = slot
            parsed.block_time = block_time
            parsed.status = TransactionStatus.FAILED if err else TransactionStatus.SUCCESS
            transactions.append(parsed)

        return transactions

    def _parse_transaction(self, tx_data: dict, wallet_address: str) -> SolanaTransaction:
        """Parse a raw transaction into a SolanaTransaction."""
        meta = tx_data.get("meta", {})
        transaction = tx_data.get("transaction", {})
        message = transaction.get("message", {})
        account_keys = message.get("accountKeys", [])

        # Extract account addresses
        addresses: list[str] = []
        for acc in account_keys:
            if isinstance(acc, str):
                addresses.append(acc)
            elif isinstance(acc, dict):
                addr = acc.get("pubkey", acc.get("account", ""))
                if addr:
                    addresses.append(addr)

        # Calculate SOL balance changes
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])

        wallet_index: Optional[int] = None
        for i, addr in enumerate(addresses):
            if addr == wallet_address:
                wallet_index = i
                break

        sol_change = 0
        if (
            wallet_index is not None
            and wallet_index < len(pre_balances)
            and wallet_index < len(post_balances)
        ):
            sol_change = post_balances[wallet_index] - pre_balances[wallet_index]

        fee = meta.get("fee", 0)

        # Determine direction
        if sol_change > 0:
            direction = TransactionDirection.INCOMING
        elif sol_change < 0:
            direction = TransactionDirection.OUTGOING
        else:
            direction = TransactionDirection.SELF

        # Identify program types and extract data
        instructions = message.get("instructions", [])
        program_type = ProgramType.UNKNOWN
        programs: list[str] = []
        counterparties: list[str] = []
        token_transfers: list[dict] = []
        memo: Optional[str] = None

        for ix in instructions:
            program_id = ix.get("programId", ix.get("program", ""))
            if isinstance(program_id, dict):
                program_id = program_id.get("pubkey", "")
            programs.append(program_id)

            parsed_ix = ix.get("parsed", {})
            if not isinstance(parsed_ix, dict):
                continue

            ix_type = parsed_ix.get("type", "")
            ix_program = parsed_ix.get("program", "")
            info = parsed_ix.get("info", {})

            if program_id == SYSTEM_PROGRAM or ix_program == "system":
                if ix_type in ("transfer", "transferChecked"):
                    program_type = ProgramType.SYSTEM_TRANSFER
                    source = info.get("source", "")
                    destination = info.get("destination", "")
                    if source and source != wallet_address:
                        counterparties.append(source)
                    if destination and destination != wallet_address:
                        counterparties.append(destination)

            elif program_id == TOKEN_PROGRAM_ID or ix_program == "spl-token":
                if program_type == ProgramType.UNKNOWN:
                    program_type = ProgramType.SPL_TOKEN
                token_amount = info.get("tokenAmount", {})
                if isinstance(token_amount, dict):
                    amount = token_amount.get("uiAmount", 0)
                else:
                    amount = token_amount
                mint = info.get("mint", "")
                authority = info.get("authority", "")
                if authority and authority != wallet_address:
                    counterparties.append(authority)
                if amount:
                    token_transfers.append({
                        "amount": amount,
                        "mint": mint,
                        "type": ix_type,
                    })

            elif program_id == STAKE_PROGRAM_ID or ix_program == "stake":
                program_type = ProgramType.STAKE

            elif program_id == MEMO_PROGRAM_ID or ix_program == "spl-memo":
                program_type = ProgramType.MEMO
                memo_text = info.get("memo", None)
                if memo_text:
                    memo = memo_text

        # Detect swaps (multiple token transfers)
        if len(token_transfers) >= 2 and program_type == ProgramType.SPL_TOKEN:
            program_type = ProgramType.SWAP

        # Deduplicate counterparties
        counterparties = list(set(c for c in counterparties if c and c != wallet_address))

        return SolanaTransaction(
            signature="",
            slot=0,
            block_time=None,
            status=TransactionStatus.SUCCESS,
            direction=direction,
            program_type=program_type,
            fee_lamports=fee,
            sol_amount=sol_change,
            token_transfers=token_transfers,
            counterparties=counterparties,
            programs=programs,
            memo=memo,
        )


class WalletError(Exception):
    """Error from wallet operations."""
    pass
