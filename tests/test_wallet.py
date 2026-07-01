"""Tests for Solana wallet client, categorizer, and importer (v0.3.0)."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_ledger.wallet import (
    SolanaWalletClient, SolanaTransaction, WalletInfo,
    ProgramType, TransactionDirection, TransactionStatus,
    WalletError, LAMPORTS_PER_SOL, SOLANA_MAINNET_RPC, SOLANA_DEVNET_RPC,
    SYSTEM_PROGRAM, TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID,
    STAKE_PROGRAM_ID, MEMO_PROGRAM_ID,
)
from agent_ledger.categorizer import (
    TransactionCategorizer, CategorizedTransaction, CategorizationRule,
    DEFAULT_CATEGORIES, KNOWN_COUNTERPARTIES,
)
from agent_ledger.importer import (
    WalletImporter, WalletSyncState, ImportResult,
    wallet_address_from_tx,
)
from agent_ledger.ledger import Ledger
from agent_ledger.storage import Storage
from agent_ledger.models import AccountType


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def ledger(tmp_dir):
    db_path = tmp_dir / "ledger.json"
    storage = Storage(db_path)
    storage.init(name="Test Wallet Ledger")
    return Ledger(storage=storage)


@pytest.fixture
def sync_dir(tmp_dir):
    return tmp_dir / "sync"


@pytest.fixture
def categorizer():
    return TransactionCategorizer()


@pytest.fixture
def importer(ledger, sync_dir):
    return WalletImporter(ledger=ledger, sync_dir=sync_dir)


# ── Wallet Constants ─────────────────────────────────────────────────

class TestWalletConstants:
    def test_lamports_per_sol(self):
        assert LAMPORTS_PER_SOL == 1_000_000_000

    def test_system_program(self):
        assert SYSTEM_PROGRAM == "11111111111111111111111111111111"

    def test_token_program_id(self):
        assert TOKEN_PROGRAM_ID == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

    def test_associated_token_program_id(self):
        assert ASSOCIATED_TOKEN_PROGRAM_ID == "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"

    def test_stake_program_id(self):
        assert STAKE_PROGRAM_ID == "Stake11111111111111111111111111111111111111"

    def test_memo_program_id(self):
        assert MEMO_PROGRAM_ID == "MemoSq4gqABaxKbGx2V8ZQpRCbfZfjC8SF1TAxzHgsP"

    def test_rpc_urls(self):
        assert "mainnet" in SOLANA_MAINNET_RPC
        assert "devnet" in SOLANA_DEVNET_RPC


# ── SolanaTransaction ────────────────────────────────────────────────

class TestSolanaTransaction:
    def test_basic_creation(self):
        tx = SolanaTransaction(
            signature="sig123",
            slot=100,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=1_000_000_000,
        )
        assert tx.signature == "sig123"
        assert tx.sol_amount_sol == 1.0
        assert tx.fee_sol == 0.000005
        assert tx.is_income is True
        assert tx.is_expense is False

    def test_outgoing(self):
        tx = SolanaTransaction(
            signature="sig456",
            slot=101,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.OUTGOING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=-2_000_000_000,
        )
        assert tx.is_income is False
        assert tx.is_expense is True
        assert tx.sol_amount_sol == -2.0

    def test_timestamp_property(self):
        tx = SolanaTransaction(
            signature="sig789",
            slot=102,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.SELF,
            program_type=ProgramType.UNKNOWN,
            fee_lamports=0,
            sol_amount=0,
        )
        ts = tx.timestamp
        assert ts is not None
        assert isinstance(ts, datetime)

    def test_timestamp_none(self):
        tx = SolanaTransaction(
            signature="sig000",
            slot=103,
            block_time=None,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.SELF,
            program_type=ProgramType.UNKNOWN,
            fee_lamports=0,
            sol_amount=0,
        )
        assert tx.timestamp is None

    def test_defaults(self):
        tx = SolanaTransaction(
            signature="sig",
            slot=0,
            block_time=None,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.SELF,
            program_type=ProgramType.UNKNOWN,
            fee_lamports=0,
            sol_amount=0,
        )
        assert tx.token_transfers == []
        assert tx.counterparties == []
        assert tx.programs == []
        assert tx.memo is None
        assert tx.raw_data is None


# ── WalletInfo ────────────────────────────────────────────────────────

class TestWalletInfo:
    def test_sol_balance(self):
        info = WalletInfo(
            address="abc123",
            network="mainnet",
            lamports=2_500_000_000,
        )
        assert info.sol_balance == 2.5

    def test_defaults(self):
        info = WalletInfo(address="abc", network="devnet")
        assert info.lamports == 0
        assert info.last_synced_slot is None
        assert info.transaction_count == 0


# ── Categorizer ───────────────────────────────────────────────────────

class TestCategorizer:
    def test_categorize_incoming_transfer(self, categorizer):
        tx = SolanaTransaction(
            signature="sig1",
            slot=100,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=1_000_000_000,
        )
        cat = categorizer.categorize(tx)
        assert cat.category == "sol_income"
        assert cat.debit_account == "sol_wallet"
        assert cat.credit_account == "sol_income"
        assert cat.amount == 1.0
        assert cat.confidence == 0.8
        assert "solana" in cat.tags

    def test_categorize_outgoing_transfer(self, categorizer):
        tx = SolanaTransaction(
            signature="sig2",
            slot=101,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.OUTGOING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=-500_000_000,
        )
        cat = categorizer.categorize(tx)
        assert cat.category == "sol_expense"
        assert cat.debit_account == "sol_expense"
        assert cat.credit_account == "sol_wallet"
        assert cat.amount == 0.5

    def test_categorize_staking_income(self, categorizer):
        tx = SolanaTransaction(
            signature="sig3",
            slot=102,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.STAKE,
            fee_lamports=0,
            sol_amount=100_000_000,
        )
        cat = categorizer.categorize(tx)
        assert cat.category == "staking_income"
        assert cat.credit_account == "staking_income"

    def test_categorize_spl_token(self, categorizer):
        tx = SolanaTransaction(
            signature="sig4",
            slot=103,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SPL_TOKEN,
            fee_lamports=5000,
            sol_amount=0,
        )
        cat = categorizer.categorize(tx)
        assert cat.category == "token_income"

    def test_categorize_self_transfer(self, categorizer):
        tx = SolanaTransaction(
            signature="sig5",
            slot=104,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.SELF,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=0,
        )
        cat = categorizer.categorize(tx)
        assert cat.debit_account == "sol_wallet"
        assert cat.credit_account == "sol_wallet"

    def test_categorize_with_counterparty(self, categorizer):
        tx = SolanaTransaction(
            signature="sig6",
            slot=105,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=1_000_000_000,
            counterparties=["675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"],
        )
        cat = categorizer.categorize(tx)
        assert "raydium" in cat.tags

    def test_categorize_with_memo(self, categorizer):
        tx = SolanaTransaction(
            signature="sig7",
            slot=106,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.MEMO,
            fee_lamports=5000,
            sol_amount=500_000_000,
            memo="x402 payment for API call",
        )
        cat = categorizer.categorize(tx)
        assert "has_memo" in cat.tags
        assert "memo" in cat.description.lower() or "x402" in cat.description

    def test_categorize_batch(self, categorizer):
        txs = [
            SolanaTransaction(
                signature=f"sig{i}",
                slot=100 + i,
                block_time=1700000000 + i,
                status=TransactionStatus.SUCCESS,
                direction=TransactionDirection.INCOMING if i % 2 == 0 else TransactionDirection.OUTGOING,
                program_type=ProgramType.SYSTEM_TRANSFER,
                fee_lamports=5000,
                sol_amount=1_000_000_000 if i % 2 == 0 else -1_000_000_000,
            )
            for i in range(5)
        ]
        results = categorizer.categorize_batch(txs)
        assert len(results) == 5
        assert results[0].category == "sol_income"
        assert results[1].category == "sol_expense"

    def test_custom_rule(self):
        rule = CategorizationRule(
            name="x402_detection",
            memo_contains="x402",
            account_code="x402_payments",
            priority=10,
        )
        cat = TransactionCategorizer(rules=[rule])
        tx = SolanaTransaction(
            signature="sig_x402",
            slot=200,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=2_000_000_000,
            memo="x402 payment received",
        )
        result = cat.categorize(tx)
        assert result.category == "x402_payments"
        assert result.confidence == 1.0

    def test_custom_rule_direction_filter(self):
        rule = CategorizationRule(
            name="outgoing_stake_only",
            program_type=ProgramType.STAKE,
            direction=TransactionDirection.OUTGOING,
            account_code="staking_delegation",
            priority=5,
        )
        cat = TransactionCategorizer(rules=[rule])
        # Should NOT match incoming
        tx_in = SolanaTransaction(
            signature="sig_in",
            slot=300,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.STAKE,
            fee_lamports=0,
            sol_amount=100_000_000,
        )
        result_in = cat.categorize(tx_in)
        assert result_in.category == "staking_income"  # Default, not custom rule

        # Should match outgoing
        tx_out = SolanaTransaction(
            signature="sig_out",
            slot=301,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.OUTGOING,
            program_type=ProgramType.STAKE,
            fee_lamports=0,
            sol_amount=-100_000_000,
        )
        result_out = cat.categorize(tx_out)
        assert result_out.category == "staking_delegation"

    def test_account_prefix(self):
        cat = TransactionCategorizer(account_prefix="sol_")
        tx = SolanaTransaction(
            signature="sig_pfx",
            slot=400,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=1_000_000_000,
        )
        result = cat.categorize(tx)
        assert result.debit_account == "sol_sol_wallet"
        assert result.credit_account == "sol_sol_income"

    def test_unknown_outgoing_maps_to_other_expense(self, categorizer):
        """UNKNOWN:outgoing is mapped to other_expense by default (not 'uncategorized')."""
        tx = SolanaTransaction(
            signature="sig_unk",
            slot=500,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.OUTGOING,
            program_type=ProgramType.UNKNOWN,
            fee_lamports=5000,
            sol_amount=-100_000_000,
        )
        result = categorizer.categorize(tx)
        # UNKNOWN:outgoing is in DEFAULT_CATEGORIES → other_expense
        assert result.category == "other_expense"
        assert result.confidence == 0.8
        assert result.debit_account == "other_expense"
        assert result.credit_account == "sol_wallet"

    def test_truly_uncategorized(self):
        """When using custom empty categories, transactions return 'uncategorized'."""
        custom_cat = TransactionCategorizer(categories={})
        tx = SolanaTransaction(
            signature="sig_unk2",
            slot=600,
            block_time=1700000001,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.OUTGOING,
            program_type=ProgramType.UNKNOWN,
            fee_lamports=5000,
            sol_amount=-100_000_000,
            counterparties=["UnknownAddr123"],
        )
        result = custom_cat.categorize(tx)
        assert result.category == "uncategorized"
        assert result.confidence == 0.3
        assert result.debit_account == "other_expense"
        assert result.credit_account == "sol_wallet"


# ── WalletSyncState ──────────────────────────────────────────────────

class TestWalletSyncState:
    def test_to_dict_and_back(self):
        state = WalletSyncState(
            wallet_address="abc123",
            network="mainnet",
            last_synced_signature="sig_last",
            last_synced_slot=12345,
            last_synced_at=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            total_imported=42,
            imported_signatures={"sig1", "sig2", "sig3"},
        )
        d = state.to_dict()
        assert d["wallet_address"] == "abc123"
        assert d["network"] == "mainnet"
        assert d["total_imported"] == 42

        restored = WalletSyncState.from_dict(d)
        assert restored.wallet_address == "abc123"
        assert restored.total_imported == 42
        assert len(restored.imported_signatures) == 3

    def test_defaults(self):
        state = WalletSyncState(wallet_address="abc", network="devnet")
        assert state.last_synced_signature is None
        assert state.total_imported == 0
        assert state.imported_signatures == set()


# ── WalletImporter ────────────────────────────────────────────────────

class TestWalletImporter:
    def test_setup_wallet_accounts(self, importer, ledger):
        created = importer.setup_wallet_accounts()
        assert len(created) > 0
        assert "sol_wallet" in created
        assert "sol_income" in created
        assert "network_fees" in created

        # Verify accounts were created
        sol_wallet = ledger.get_account("sol_wallet")
        assert sol_wallet.account_type == AccountType.ASSET

        sol_income = ledger.get_account("sol_income")
        assert sol_income.account_type == AccountType.REVENUE

        network_fees = ledger.get_account("network_fees")
        assert network_fees.account_type == AccountType.EXPENSE

    def test_setup_wallet_accounts_idempotent(self, importer):
        created1 = importer.setup_wallet_accounts()
        created2 = importer.setup_wallet_accounts()
        assert len(created2) == 0  # All already exist

    def test_sync_state_persistence(self, importer, sync_dir):
        state = WalletSyncState(
            wallet_address="test_wallet",
            network="mainnet",
            last_synced_signature="sig_last",
            last_synced_slot=100,
            total_imported=5,
            imported_signatures={"sig1", "sig2"},
        )
        importer._save_sync_state(state)

        loaded = importer._load_sync_state("test_wallet", "mainnet")
        assert loaded.wallet_address == "test_wallet"
        assert loaded.total_imported == 5
        assert "sig1" in loaded.imported_signatures

    def test_sync_wallet_dry_run(self, importer):
        """Test dry run doesn't post entries."""
        mock_tx = SolanaTransaction(
            signature="dry_run_sig",
            slot=100,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=1_000_000_000,
        )

        mock_client = MagicMock()
        mock_client.fetch_transactions.return_value = [mock_tx]
        importer._client = mock_client

        result = importer.sync_wallet(
            wallet_address="test_addr",
            network="mainnet",
            dry_run=True,
            create_accounts=False,
        )
        assert result.transactions_imported == 1
        assert len(result.entries_created) == 0  # Dry run

    def test_sync_wallet_with_mock(self, importer, ledger):
        """Test full sync with mocked wallet client."""
        # Setup accounts first
        importer.setup_wallet_accounts()

        mock_tx = SolanaTransaction(
            signature="real_sig_1",
            slot=200,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=1_000_000_000,
            counterparties=["counterparty1"],
        )

        mock_client = MagicMock()
        mock_client.fetch_transactions.return_value = [mock_tx]
        importer._client = mock_client

        result = importer.sync_wallet(
            wallet_address="test_addr",
            network="mainnet",
            create_accounts=False,
            import_fees=True,
        )
        assert result.transactions_imported == 1
        assert len(result.entries_created) >= 1
        assert result.total_sol_imported == 1.0

    def test_sync_skips_failed_transactions(self, importer, ledger):
        """Failed transactions should be skipped."""
        importer.setup_wallet_accounts()

        failed_tx = SolanaTransaction(
            signature="failed_sig",
            slot=300,
            block_time=1700000000,
            status=TransactionStatus.FAILED,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=1_000_000_000,
        )

        mock_client = MagicMock()
        mock_client.fetch_transactions.return_value = [failed_tx]
        importer._client = mock_client

        result = importer.sync_wallet(
            wallet_address="test_addr",
            network="mainnet",
            create_accounts=False,
        )
        assert result.transactions_imported == 0
        assert result.transactions_skipped == 1

    def test_sync_skips_already_imported(self, importer, ledger):
        """Already imported transactions should be skipped."""
        importer.setup_wallet_accounts()

        tx = SolanaTransaction(
            signature="already_imported_sig",
            slot=400,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=1_000_000_000,
        )

        mock_client = MagicMock()
        mock_client.fetch_transactions.return_value = [tx]
        importer._client = mock_client

        # First import
        result1 = importer.sync_wallet("test_addr", network="mainnet", create_accounts=False)
        assert result1.transactions_imported == 1

        # Second import (same transaction)
        mock_client2 = MagicMock()
        mock_client2.fetch_transactions.return_value = [tx]
        importer._client = mock_client2
        result2 = importer.sync_wallet("test_addr", network="mainnet", create_accounts=False)
        assert result2.transactions_skipped == 1
        assert result2.transactions_imported == 0

    def test_wallet_config_save_load(self, importer, sync_dir):
        importer._save_wallet_config("my_wallet", "mainnet")
        config = importer.load_wallet_config()
        assert config is not None
        assert config["wallet_address"] == "my_wallet"
        assert config["network"] == "mainnet"

    def test_wallet_config_missing(self, importer):
        config = importer.load_wallet_config()
        assert config is None


# ── WalletImporter Account Creation ──────────────────────────────────

class TestImporterAccountCreation:
    def test_ensure_account(self, importer, ledger):
        importer._ensure_account("new_asset_acct")
        acct = ledger.get_account("new_asset_acct")
        assert acct is not None

    def test_infer_account_type(self):
        assert WalletImporter._infer_account_type("sol_wallet") == AccountType.ASSET
        assert WalletImporter._infer_account_type("sol_income") == AccountType.REVENUE
        assert WalletImporter._infer_account_type("network_fees") == AccountType.EXPENSE
        assert WalletImporter._infer_account_type("liability_acct") == AccountType.LIABILITY
        assert WalletImporter._infer_account_type("random_code") == AccountType.EXPENSE


# ── Helper Functions ─────────────────────────────────────────────────

class TestHelperFunctions:
    def test_wallet_address_from_tx_no_raw_data(self):
        tx = SolanaTransaction(
            signature="sig", slot=0, block_time=None,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.SELF,
            program_type=ProgramType.UNKNOWN,
            fee_lamports=0, sol_amount=0,
        )
        assert wallet_address_from_tx(tx) == ""

    def test_wallet_address_from_tx_with_raw_data(self):
        tx = SolanaTransaction(
            signature="sig", slot=0, block_time=None,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.SELF,
            program_type=ProgramType.UNKNOWN,
            fee_lamports=0, sol_amount=0,
            raw_data={"wallet_address": "abc123"},
        )
        assert wallet_address_from_tx(tx) == "abc123"


# ── Integration: Full Pipeline ───────────────────────────────────────

class TestFullPipeline:
    def test_categorize_and_import_incoming(self, ledger, sync_dir):
        """Test the full pipeline: create tx → categorize → import → verify journal entry."""
        # Setup
        importer = WalletImporter(ledger=ledger, sync_dir=sync_dir)
        importer.setup_wallet_accounts()

        # Create a transaction
        tx = SolanaTransaction(
            signature="pipeline_sig_1",
            slot=500,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.INCOMING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=5000,
            sol_amount=3_000_000_000,  # 3 SOL incoming
            counterparties=["sender123"],
        )

        # Sync with mock
        mock_client = MagicMock()
        mock_client.fetch_transactions.return_value = [tx]
        importer._client = mock_client
        result = importer.sync_wallet("test_wallet", network="mainnet", create_accounts=False)

        assert result.transactions_imported == 1
        assert result.total_sol_imported == 3.0

        # Verify the journal entry was created
        entries = ledger.list_entries()
        assert len(entries) >= 1

        # Check the entry has proper lines
        entry = entries[0]
        assert entry.lines is not None

    def test_categorize_and_import_outgoing_with_fee(self, ledger, sync_dir):
        """Test outgoing transaction with separate fee entry."""
        importer = WalletImporter(ledger=ledger, sync_dir=sync_dir)
        importer.setup_wallet_accounts()

        tx = SolanaTransaction(
            signature="outgoing_sig",
            slot=600,
            block_time=1700000000,
            status=TransactionStatus.SUCCESS,
            direction=TransactionDirection.OUTGOING,
            program_type=ProgramType.SYSTEM_TRANSFER,
            fee_lamports=50_000_000,  # 0.05 SOL — survives 2-decimal rounding
            sol_amount=-2_000_000_000,  # 2 SOL outgoing
            counterparties=["receiver456"],
        )

        mock_client = MagicMock()
        mock_client.fetch_transactions.return_value = [tx]
        importer._client = mock_client
        result = importer.sync_wallet("test_wallet", network="mainnet", create_accounts=False)

        assert result.transactions_imported == 1
        assert result.total_sol_imported == 2.0
        # Should have at least 2 entries: main + fee
        assert len(result.entries_created) >= 2

    def test_multiple_transactions(self, ledger, sync_dir):
        """Test importing multiple transactions at once."""
        importer = WalletImporter(ledger=ledger, sync_dir=sync_dir)
        importer.setup_wallet_accounts()

        txs = [
            SolanaTransaction(
                signature=f"multi_sig_{i}",
                slot=700 + i,
                block_time=1700000000 + i * 100,
                status=TransactionStatus.SUCCESS,
                direction=TransactionDirection.INCOMING if i % 2 == 0 else TransactionDirection.OUTGOING,
                program_type=ProgramType.SYSTEM_TRANSFER,
                fee_lamports=5000,
                sol_amount=1_000_000_000 if i % 2 == 0 else -500_000_000,
            )
            for i in range(4)
        ]

        mock_client = MagicMock()
        mock_client.fetch_transactions.return_value = txs
        importer._client = mock_client
        result = importer.sync_wallet("multi_wallet", network="mainnet", create_accounts=False)

        assert result.transactions_imported == 4
        assert len(result.entries_created) >= 4


# ── Default Categories Coverage ──────────────────────────────────────

class TestDefaultCategories:
    def test_all_program_types_have_categories(self):
        """Ensure every program type + direction has a default category."""
        for pt in ProgramType:
            for direction in [TransactionDirection.INCOMING, TransactionDirection.OUTGOING]:
                key = f"{pt.value}:{direction.value}"
                assert key in DEFAULT_CATEGORIES, f"Missing category for {key}"

    def test_known_counterparties_not_empty(self):
        assert len(KNOWN_COUNTERPARTIES) > 0
