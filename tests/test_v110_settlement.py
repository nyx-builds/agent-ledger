"""Tests for v1.1.0 Settlement & Netting Engine."""

import pytest
import tempfile
from pathlib import Path

from agent_ledger.models import AccountType
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.settlement import (
    SettlementEngine,
    SettlementBatch,
    SettlementItem,
    SettlementItemType,
    SettlementStatus,
    NetPositionDirection,
    NetPosition,
    NetPayment,
    SettlementProof,
)
from agent_ledger.exceptions import (
    SettlementNotFoundError,
    SettlementItemNotFoundError,
    InvalidSettlementStateError,
    DuplicateSettlementItemError,
    LedgerError,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Fresh settlement engine."""
    return SettlementEngine()


@pytest.fixture
def ledger():
    """Fresh ledger with basic accounts."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    path.unlink()
    try:
        storage = Storage(path)
        storage.init(name="Test Ledger", base_currency="USD")
        ledger = Ledger(storage)
        yield ledger
    finally:
        path.unlink(missing_ok=True)


@pytest.fixture
def batch_with_items(engine):
    """A batch with a few items."""
    batch = engine.create_batch("Test Batch", currency="USD")
    engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
    engine.add_item(batch.id, "agent_b", "agent_a", 60.0)
    engine.add_item(batch.id, "agent_b", "agent_c", 50.0)
    return batch, engine


# ════════════════════════════════════════════════════════════════════
# BATCH LIFECYCLE TESTS
# ════════════════════════════════════════════════════════════════════


class TestSettlementEnums:
    def test_status_values(self):
        assert SettlementStatus.DRAFT.value == "draft"
        assert SettlementStatus.CALCULATED.value == "calculated"
        assert SettlementStatus.SETTLED.value == "settled"
        assert SettlementStatus.DISPUTED.value == "disputed"
        assert SettlementStatus.CANCELLED.value == "cancelled"

    def test_item_type_values(self):
        assert SettlementItemType.INVOICE.value == "invoice"
        assert SettlementItemType.LOAN.value == "loan"
        assert SettlementItemType.SERVICE_FEE.value == "service_fee"
        assert SettlementItemType.REFUND.value == "refund"
        assert SettlementItemType.COMMISSION.value == "commission"
        assert SettlementItemType.CUSTOM.value == "custom"

    def test_direction_values(self):
        assert NetPositionDirection.OWES.value == "owes"
        assert NetPositionDirection.OWED.value == "owed"
        assert NetPositionDirection.EVEN.value == "even"


class TestCreateBatch:
    def test_create_batch_defaults(self, engine):
        batch = engine.create_batch()
        assert batch.id
        assert batch.name  # auto-generated
        assert batch.currency == "USD"
        assert batch.status == SettlementStatus.DRAFT
        assert batch.items == []
        assert batch.net_positions == []
        assert batch.net_payments == []
        assert batch.proof is None

    def test_create_batch_with_name(self, engine):
        batch = engine.create_batch(name="Weekly Netting")
        assert batch.name == "Weekly Netting"

    def test_create_batch_with_description(self, engine):
        batch = engine.create_batch(description="Settling agent transactions")
        assert batch.description == "Settling agent transactions"

    def test_create_batch_with_currency(self, engine):
        batch = engine.create_batch(currency="EUR")
        assert batch.currency == "EUR"

    def test_create_batch_with_metadata(self, engine):
        batch = engine.create_batch(metadata={"period": "2026-07"})
        assert batch.metadata == {"period": "2026-07"}

    def test_get_batch(self, engine):
        batch = engine.create_batch(name="Test")
        fetched = engine.get_batch(batch.id)
        assert fetched.id == batch.id
        assert fetched.name == "Test"

    def test_get_batch_not_found(self, engine):
        with pytest.raises(SettlementNotFoundError):
            engine.get_batch("nonexistent-id")

    def test_list_batches_empty(self, engine):
        assert engine.list_batches() == []

    def test_list_batches_multiple(self, engine):
        b1 = engine.create_batch(name="B1")
        b2 = engine.create_batch(name="B2")
        b3 = engine.create_batch(name="B3")
        batches = engine.list_batches()
        assert len(batches) == 3
        # Most recent first
        assert batches[0].id == b3.id

    def test_list_batches_filter_by_status(self, engine):
        b1 = engine.create_batch(name="B1")
        b2 = engine.create_batch(name="B2")
        # b2 gets cancelled
        engine.cancel_batch(b2.id)
        drafts = engine.list_batches(status=SettlementStatus.DRAFT)
        assert len(drafts) == 1
        assert drafts[0].id == b1.id

    def test_cancel_batch(self, engine):
        batch = engine.create_batch(name="To Cancel")
        cancelled = engine.cancel_batch(batch.id)
        assert cancelled.status == SettlementStatus.CANCELLED

    def test_cancel_settled_batch_fails(self, engine):
        batch = engine.create_batch(name="Settled")
        engine.add_item(batch.id, "a", "b", 10)
        engine.calculate_netting(batch.id)
        engine.settle(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.cancel_batch(batch.id)

    def test_cancel_already_cancelled_fails(self, engine):
        batch = engine.create_batch(name="Cancelled")
        engine.cancel_batch(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.cancel_batch(batch.id)

    def test_delete_batch_draft(self, engine):
        batch = engine.create_batch(name="To Delete")
        engine.delete_batch(batch.id)
        with pytest.raises(SettlementNotFoundError):
            engine.get_batch(batch.id)

    def test_delete_batch_calculated_fails(self, engine):
        batch = engine.create_batch(name="Calculated")
        engine.add_item(batch.id, "a", "b", 10)
        engine.calculate_netting(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.delete_batch(batch.id)

    def test_delete_cancelled_batch(self, engine):
        batch = engine.create_batch(name="Cancelled")
        engine.cancel_batch(batch.id)
        engine.delete_batch(batch.id)
        with pytest.raises(SettlementNotFoundError):
            engine.get_batch(batch.id)


# ════════════════════════════════════════════════════════════════════
# ITEM MANAGEMENT TESTS
# ════════════════════════════════════════════════════════════════════


class TestAddItem:
    def test_add_item_basic(self, engine):
        batch = engine.create_batch(name="Test")
        item = engine.add_item(
            batch.id,
            payer="agent_a",
            payee="agent_b",
            amount=100.0,
        )
        assert item.payer == "agent_a"
        assert item.payee == "agent_b"
        assert item.amount == 100.0
        assert item.currency == "USD"
        assert item.item_type == SettlementItemType.CUSTOM
        assert item.id
        assert len(batch.items) == 1

    def test_add_item_normalizes_case(self, engine):
        batch = engine.create_batch(name="Test")
        item = engine.add_item(batch.id, "Agent_A", "Agent_B", 100.0)
        assert item.payer == "agent_a"
        assert item.payee == "agent_b"

    def test_add_item_with_type(self, engine):
        batch = engine.create_batch(name="Test")
        item = engine.add_item(
            batch.id, "a", "b", 50.0,
            item_type=SettlementItemType.INVOICE,
        )
        assert item.item_type == SettlementItemType.INVOICE

    def test_add_item_with_reference(self, engine):
        batch = engine.create_batch(name="Test")
        item = engine.add_item(
            batch.id, "a", "b", 50.0,
            reference="INV-001",
        )
        assert item.reference == "INV-001"

    def test_add_item_with_description(self, engine):
        batch = engine.create_batch(name="Test")
        item = engine.add_item(
            batch.id, "a", "b", 50.0,
            description="Payment for data processing",
        )
        assert item.description == "Payment for data processing"

    def test_add_item_zero_amount_fails(self, engine):
        batch = engine.create_batch(name="Test")
        with pytest.raises(LedgerError, match="positive"):
            engine.add_item(batch.id, "a", "b", 0)

    def test_add_item_negative_amount_fails(self, engine):
        batch = engine.create_batch(name="Test")
        with pytest.raises(LedgerError, match="positive"):
            engine.add_item(batch.id, "a", "b", -10)

    def test_add_item_same_payer_payee_fails(self, engine):
        batch = engine.create_batch(name="Test")
        with pytest.raises(LedgerError, match="different parties"):
            engine.add_item(batch.id, "a", "a", 100)

    def test_add_item_empty_payer_fails(self, engine):
        batch = engine.create_batch(name="Test")
        with pytest.raises(LedgerError):
            engine.add_item(batch.id, "", "b", 100)

    def test_add_item_duplicate_reference_fails(self, engine):
        batch = engine.create_batch(name="Test")
        engine.add_item(batch.id, "a", "b", 100, reference="INV-001")
        with pytest.raises(DuplicateSettlementItemError):
            engine.add_item(batch.id, "c", "d", 50, reference="INV-001")

    def test_add_item_no_reference_allows_duplicates(self, engine):
        batch = engine.create_batch(name="Test")
        engine.add_item(batch.id, "a", "b", 100)
        # No error — no reference to check
        engine.add_item(batch.id, "c", "d", 50)

    def test_add_item_to_settled_batch_fails(self, engine):
        batch = engine.create_batch(name="Test")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        engine.settle(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.add_item(batch.id, "c", "d", 50)


class TestRemoveItem:
    def test_remove_item(self, engine):
        batch = engine.create_batch(name="Test")
        item = engine.add_item(batch.id, "a", "b", 100)
        assert len(batch.items) == 1
        engine.remove_item(batch.id, item.id)
        assert len(batch.items) == 0

    def test_remove_item_not_found(self, engine):
        batch = engine.create_batch(name="Test")
        with pytest.raises(SettlementItemNotFoundError):
            engine.remove_item(batch.id, "nonexistent")

    def test_remove_item_from_settled_fails(self, engine):
        batch = engine.create_batch(name="Test")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        engine.settle(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.remove_item(batch.id, batch.items[0].id)


# ════════════════════════════════════════════════════════════════════
# DISPUTE TESTS
# ════════════════════════════════════════════════════════════════════


class TestDispute:
    def test_dispute_item(self, engine):
        batch = engine.create_batch(name="Test")
        item = engine.add_item(batch.id, "a", "b", 100)
        disputed = engine.dispute_item(batch.id, item.id, "Wrong amount")
        assert disputed.disputed is True
        assert disputed.dispute_reason == "Wrong amount"
        assert batch.status == SettlementStatus.DISPUTED

    def test_dispute_item_not_found(self, engine):
        batch = engine.create_batch(name="Test")
        with pytest.raises(SettlementItemNotFoundError):
            engine.dispute_item(batch.id, "nonexistent", "reason")

    def test_dispute_on_settled_fails(self, engine):
        batch = engine.create_batch(name="Test")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        engine.settle(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.dispute_item(batch.id, batch.items[0].id, "reason")

    def test_resolve_dispute(self, engine):
        batch = engine.create_batch(name="Test")
        item = engine.add_item(batch.id, "a", "b", 100)
        engine.dispute_item(batch.id, item.id, "Wrong amount")
        resolved = engine.resolve_dispute(batch.id, item.id)
        assert resolved.disputed is False
        assert resolved.dispute_reason == ""
        # Batch back to draft when all disputes resolved
        assert batch.status == SettlementStatus.DRAFT

    def test_resolve_dispute_partial(self, engine):
        batch = engine.create_batch(name="Test")
        item1 = engine.add_item(batch.id, "a", "b", 100)
        item2 = engine.add_item(batch.id, "c", "d", 50)
        engine.dispute_item(batch.id, item1.id, "Reason 1")
        engine.dispute_item(batch.id, item2.id, "Reason 2")
        assert batch.status == SettlementStatus.DISPUTED
        # Resolve only one
        engine.resolve_dispute(batch.id, item1.id)
        # Still disputed because item2 is disputed
        assert batch.status == SettlementStatus.DISPUTED

    def test_get_disputed_items(self, engine):
        batch = engine.create_batch(name="Test")
        item1 = engine.add_item(batch.id, "a", "b", 100)
        item2 = engine.add_item(batch.id, "c", "d", 50)
        engine.dispute_item(batch.id, item1.id, "Wrong")
        disputed = engine.get_disputed_items(batch.id)
        assert len(disputed) == 1
        assert disputed[0].id == item1.id


# ════════════════════════════════════════════════════════════════════
# NETTING ENGINE TESTS
# ════════════════════════════════════════════════════════════════════


class TestNetting:
    def test_simple_bilateral_netting(self, engine):
        """A owes B $100, B owes A $60 → net: A pays B $40."""
        batch = engine.create_batch(name="Bilateral")
        engine.add_item(batch.id, "a", "b", 100)
        engine.add_item(batch.id, "b", "a", 60)
        result = engine.calculate_netting(batch.id)

        assert result.status == SettlementStatus.CALCULATED
        assert len(result.net_payments) == 1
        payment = result.net_payments[0]
        assert payment.payer == "a"
        assert payment.payee == "b"
        assert payment.amount == 40.0

    def test_netting_gross_vs_net(self, engine):
        batch = engine.create_batch(name="Savings")
        engine.add_item(batch.id, "a", "b", 100)
        engine.add_item(batch.id, "b", "a", 60)
        result = engine.calculate_netting(batch.id)

        proof = result.proof
        assert proof.total_gross_volume == 160.0  # 100 + 60
        assert proof.total_net_volume == 40.0     # only 1 net payment of 40
        assert proof.netted_savings == 120.0       # 160 - 40 = 120 saved

    def test_three_party_netting(self, engine):
        """A owes B $100, B owes C $50, C owes A $30.
        Net: A owes net = 100 - 30 = 70 (owes)
             B: owes 50, owed 100 → net +50 (owed)
             C: owes 30, owed 50 → net +20 (owed)
        Net payments: A → B $50, A → C $20 (greedy)
        """
        batch = engine.create_batch(name="Triangular")
        engine.add_item(batch.id, "a", "b", 100)
        engine.add_item(batch.id, "b", "c", 50)
        engine.add_item(batch.id, "c", "a", 30)
        result = engine.calculate_netting(batch.id)

        positions = {p.party: p for p in result.net_positions}
        assert positions["a"].direction == NetPositionDirection.OWES
        assert positions["a"].net == -70.0
        assert positions["b"].direction == NetPositionDirection.OWED
        assert positions["b"].net == 50.0
        assert positions["c"].direction == NetPositionDirection.OWED
        assert positions["c"].net == 20.0

        # Total net payments = 70
        total = sum(p.amount for p in result.net_payments)
        assert total == 70.0

    def test_netting_all_even(self, engine):
        """A owes B $50, B owes A $50 → net zero, no payments."""
        batch = engine.create_batch(name="Even")
        engine.add_item(batch.id, "a", "b", 50)
        engine.add_item(batch.id, "b", "a", 50)
        result = engine.calculate_netting(batch.id)

        positions = {p.party: p for p in result.net_positions}
        assert positions["a"].direction == NetPositionDirection.EVEN
        assert positions["a"].net == 0.0
        assert positions["b"].direction == NetPositionDirection.EVEN
        assert positions["b"].net == 0.0
        assert len(result.net_payments) == 0

    def test_netting_multiple_items_same_pair(self, engine):
        """Multiple items between same pair should aggregate."""
        batch = engine.create_batch(name="Multi")
        engine.add_item(batch.id, "a", "b", 30)
        engine.add_item(batch.id, "a", "b", 40)
        engine.add_item(batch.id, "b", "a", 20)
        result = engine.calculate_netting(batch.id)

        # A owes B: 30 + 40 = 70; B owes A: 20; net: A pays B 50
        assert len(result.net_payments) == 1
        assert result.net_payments[0].payer == "a"
        assert result.net_payments[0].payee == "b"
        assert result.net_payments[0].amount == 50.0

    def test_netting_empty_batch_fails(self, engine):
        batch = engine.create_batch(name="Empty")
        with pytest.raises(InvalidSettlementStateError, match="empty"):
            engine.calculate_netting(batch.id)

    def test_netting_already_settled_fails(self, engine):
        batch = engine.create_batch(name="Settled")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        engine.settle(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.calculate_netting(batch.id)

    def test_netting_cancelled_batch_fails(self, engine):
        batch = engine.create_batch(name="Cancelled")
        engine.add_item(batch.id, "a", "b", 100)
        engine.cancel_batch(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.calculate_netting(batch.id)

    def test_netting_excludes_disputed(self, engine):
        """Disputed items should be excluded from netting."""
        batch = engine.create_batch(name="Disputed")
        engine.add_item(batch.id, "a", "b", 100)
        disputed_item = engine.add_item(batch.id, "b", "a", 80)
        engine.dispute_item(batch.id, disputed_item.id, "Disagreed")

        result = engine.calculate_netting(batch.id)

        # Only undisputed: a→b $100
        assert result.proof.item_count == 1
        assert len(result.net_payments) == 1
        assert result.net_payments[0].amount == 100.0

    def test_netting_all_disputed_fails(self, engine):
        batch = engine.create_batch(name="All Disputed")
        item = engine.add_item(batch.id, "a", "b", 100)
        engine.dispute_item(batch.id, item.id, "Nope")
        with pytest.raises(InvalidSettlementStateError, match="disputed"):
            engine.calculate_netting(batch.id)

    def test_proof_hash_generated(self, engine):
        batch = engine.create_batch(name="Proof")
        engine.add_item(batch.id, "a", "b", 100)
        result = engine.calculate_netting(batch.id)
        assert result.proof is not None
        assert result.proof.proof_hash
        assert len(result.proof.proof_hash) == 16

    def test_proof_participant_count(self, engine):
        batch = engine.create_batch(name="Participants")
        engine.add_item(batch.id, "a", "b", 100)
        engine.add_item(batch.id, "b", "c", 50)
        engine.add_item(batch.id, "c", "a", 30)
        result = engine.calculate_netting(batch.id)
        assert result.proof.participant_count == 3

    def test_large_netting_chain(self, engine):
        """Chain of 5 agents."""
        batch = engine.create_batch(name="Chain")
        # a→b→c→d→e, each owes the next $10
        for i, (payer, payee) in enumerate([("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")]):
            engine.add_item(batch.id, payer, payee, 10.0)
        result = engine.calculate_netting(batch.id)

        # a owes net 10, e is owed net 10, b/c/d are even
        positions = {p.party: p for p in result.net_positions}
        assert positions["a"].direction == NetPositionDirection.OWES
        assert positions["e"].direction == NetPositionDirection.OWED
        assert positions["b"].direction == NetPositionDirection.EVEN
        assert positions["c"].direction == NetPositionDirection.EVEN
        assert positions["d"].direction == NetPositionDirection.EVEN


# ════════════════════════════════════════════════════════════════════
# SETTLE TESTS
# ════════════════════════════════════════════════════════════════════


class TestSettle:
    def test_settle_batch(self, engine):
        batch = engine.create_batch(name="To Settle")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        result = engine.settle(batch.id)

        assert result.status == SettlementStatus.SETTLED
        assert result.settled_at is not None
        assert result.proof.settled_at is not None

    def test_settle_without_calc_fails(self, engine):
        batch = engine.create_batch(name="Draft")
        engine.add_item(batch.id, "a", "b", 100)
        with pytest.raises(InvalidSettlementStateError):
            engine.settle(batch.id)

    def test_settle_already_settled_fails(self, engine):
        batch = engine.create_batch(name="Double Settle")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        engine.settle(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.settle(batch.id)

    def test_settle_with_disputed(self, engine):
        batch = engine.create_batch(name="Disputed Settlement")
        engine.add_item(batch.id, "a", "b", 100)
        disputed = engine.add_item(batch.id, "b", "a", 80)
        engine.dispute_item(batch.id, disputed.id, "Wrong")
        # Calculate with disputed items
        engine.calculate_netting(batch.id)
        assert batch.status == SettlementStatus.DISPUTED
        # Can settle (disputed items excluded)
        result = engine.settle(batch.id)
        assert result.status == SettlementStatus.SETTLED

    def test_proof_hash_changes_after_settle(self, engine):
        batch = engine.create_batch(name="Hash Test")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        hash_before = batch.proof.proof_hash
        engine.settle(batch.id)
        hash_after = batch.proof.proof_hash
        # Hash should change because settled_at is included
        assert hash_before != hash_after


# ════════════════════════════════════════════════════════════════════
# QUERY TESTS
# ════════════════════════════════════════════════════════════════════


class TestQueries:
    def test_get_party_summary(self, engine):
        batch = engine.create_batch(name="Summary")
        engine.add_item(batch.id, "a", "b", 100)
        engine.add_item(batch.id, "b", "a", 60)
        pos = engine.get_party_summary(batch.id, "a")
        assert pos.party == "a"
        assert pos.gross_out == 100.0
        assert pos.gross_in == 60.0
        assert pos.net == -40.0  # owes 40 net
        assert pos.direction == NetPositionDirection.OWES

    def test_get_party_summary_case_insensitive(self, engine):
        batch = engine.create_batch(name="Summary")
        engine.add_item(batch.id, "a", "b", 100)
        # Lookup with different case should find the party
        pos = engine.get_party_summary(batch.id, "A")
        assert pos.party == "a"  # stored lowercase
        assert pos.gross_out == 100.0

    def test_get_party_summary_nonexistent_party(self, engine):
        batch = engine.create_batch(name="Summary")
        engine.add_item(batch.id, "a", "b", 100)
        pos = engine.get_party_summary(batch.id, "unknown")
        assert pos.party == "unknown"
        assert pos.gross_out == 0.0
        assert pos.gross_in == 0.0
        assert pos.direction == NetPositionDirection.EVEN

    def test_verify_proof_correct(self, engine):
        batch = engine.create_batch(name="Verify")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        assert engine.verify_proof(batch.id, batch.proof.proof_hash) is True

    def test_verify_proof_wrong_hash(self, engine):
        batch = engine.create_batch(name="Verify")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        assert engine.verify_proof(batch.id, "wronghash12345") is False

    def test_verify_proof_no_proof(self, engine):
        batch = engine.create_batch(name="No Proof")
        engine.add_item(batch.id, "a", "b", 100)
        assert engine.verify_proof(batch.id, "anything") is False


# ════════════════════════════════════════════════════════════════════
# SERIALIZATION TESTS
# ════════════════════════════════════════════════════════════════════


class TestSerialization:
    def test_to_dict_and_from_dict(self, engine):
        batch = engine.create_batch(name="Persist Test", currency="EUR")
        engine.add_item(batch.id, "a", "b", 100)
        engine.add_item(batch.id, "b", "a", 60)
        engine.calculate_netting(batch.id)

        data = engine.to_dict()
        engine2 = SettlementEngine()
        engine2.from_dict(data)

        batches = engine2.list_batches()
        assert len(batches) == 1
        restored = batches[0]
        assert restored.name == "Persist Test"
        assert restored.currency == "EUR"
        assert restored.status == SettlementStatus.CALCULATED
        assert len(restored.items) == 2
        assert len(restored.net_positions) > 0
        assert restored.proof is not None
        assert restored.proof.proof_hash == batch.proof.proof_hash

    def test_round_trip_preserves_payments(self, engine):
        batch = engine.create_batch(name="Payments")
        engine.add_item(batch.id, "a", "b", 100)
        engine.add_item(batch.id, "b", "c", 50)
        engine.calculate_netting(batch.id)

        data = engine.to_dict()
        engine2 = SettlementEngine()
        engine2.from_dict(data)

        restored = engine2.list_batches()[0]
        assert len(restored.net_payments) == len(batch.net_payments)
        for orig, rest in zip(batch.net_payments, restored.net_payments):
            assert orig.payer == rest.payer
            assert orig.payee == rest.payee
            assert orig.amount == rest.amount

    def test_serialization_with_settled(self, engine):
        batch = engine.create_batch(name="Settled Persist")
        engine.add_item(batch.id, "a", "b", 100)
        engine.calculate_netting(batch.id)
        engine.settle(batch.id)

        data = engine.to_dict()
        engine2 = SettlementEngine()
        engine2.from_dict(data)

        restored = engine2.list_batches()[0]
        assert restored.status == SettlementStatus.SETTLED
        assert restored.settled_at is not None


# ════════════════════════════════════════════════════════════════════
# MCP SERVER INTEGRATION TESTS
# ════════════════════════════════════════════════════════════════════


class TestMCPServerSettlement:
    def test_mcp_create_settlement_batch(self, ledger):
        from agent_ledger.mcp_server import handle_tool_call

        result = handle_tool_call(ledger, "create_settlement_batch", {
            "name": "Weekly Netting",
            "currency": "USD",
        })
        text = result[0]["text"]
        import json
        data = json.loads(text)
        assert data["name"] == "Weekly Netting"
        assert data["status"] == "draft"
        assert data["id"]

    def test_mcp_add_settlement_item(self, ledger):
        from agent_ledger.mcp_server import handle_tool_call

        # Create batch first
        result = handle_tool_call(ledger, "create_settlement_batch", {
            "name": "Test",
        })
        import json
        batch_data = json.loads(result[0]["text"])
        batch_id = batch_data["id"]

        # Add item
        result = handle_tool_call(ledger, "add_settlement_item", {
            "batch_id": batch_id,
            "payer": "agent_a",
            "payee": "agent_b",
            "amount": 100,
        })
        data = json.loads(result[0]["text"])
        assert data["payer"] == "agent_a"
        assert data["payee"] == "agent_b"
        assert data["amount"] == 100

    def test_mcp_calculate_netting(self, ledger):
        from agent_ledger.mcp_server import handle_tool_call
        import json

        # Create batch
        result = handle_tool_call(ledger, "create_settlement_batch", {})
        batch_id = json.loads(result[0]["text"])["id"]

        # Add items
        handle_tool_call(ledger, "add_settlement_item", {
            "batch_id": batch_id,
            "payer": "a",
            "payee": "b",
            "amount": 100,
        })
        handle_tool_call(ledger, "add_settlement_item", {
            "batch_id": batch_id,
            "payer": "b",
            "payee": "a",
            "amount": 60,
        })

        # Calculate netting
        result = handle_tool_call(ledger, "calculate_settlement_netting", {
            "batch_id": batch_id,
        })
        data = json.loads(result[0]["text"])
        assert data["status"] == "calculated"
        assert data["proof"] is not None
        # Total gross = sum of all payer amounts = 100 + 60 = 160
        assert data["proof"]["total_gross_volume"] == 160.0

    def test_mcp_get_party_position(self, ledger):
        from agent_ledger.mcp_server import handle_tool_call
        import json

        result = handle_tool_call(ledger, "create_settlement_batch", {})
        batch_id = json.loads(result[0]["text"])["id"]

        handle_tool_call(ledger, "add_settlement_item", {
            "batch_id": batch_id,
            "payer": "a",
            "payee": "b",
            "amount": 100,
        })

        result = handle_tool_call(ledger, "get_party_position", {
            "batch_id": batch_id,
            "party": "a",
        })
        data = json.loads(result[0]["text"])
        assert data["party"] == "a"
        assert data["direction"] == "owes"

    def test_mcp_list_batches(self, ledger):
        from agent_ledger.mcp_server import handle_tool_call
        import json

        handle_tool_call(ledger, "create_settlement_batch", {"name": "B1"})
        handle_tool_call(ledger, "create_settlement_batch", {"name": "B2"})

        result = handle_tool_call(ledger, "list_settlement_batches", {})
        data = json.loads(result[0]["text"])
        assert len(data) == 2

    def test_mcp_settle_batch(self, ledger):
        from agent_ledger.mcp_server import handle_tool_call
        import json

        result = handle_tool_call(ledger, "create_settlement_batch", {})
        batch_id = json.loads(result[0]["text"])["id"]

        handle_tool_call(ledger, "add_settlement_item", {
            "batch_id": batch_id,
            "payer": "a",
            "payee": "b",
            "amount": 100,
        })
        handle_tool_call(ledger, "calculate_settlement_netting", {
            "batch_id": batch_id,
        })
        result = handle_tool_call(ledger, "settle_batch", {
            "batch_id": batch_id,
        })
        data = json.loads(result[0]["text"])
        assert data["status"] == "settled"

    def test_mcp_dispute_item(self, ledger):
        from agent_ledger.mcp_server import handle_tool_call
        import json

        result = handle_tool_call(ledger, "create_settlement_batch", {})
        batch_id = json.loads(result[0]["text"])["id"]

        result = handle_tool_call(ledger, "add_settlement_item", {
            "batch_id": batch_id,
            "payer": "a",
            "payee": "b",
            "amount": 100,
        })
        item_id = json.loads(result[0]["text"])["id"]

        result = handle_tool_call(ledger, "dispute_settlement_item", {
            "batch_id": batch_id,
            "item_id": item_id,
            "reason": "Incorrect amount",
        })
        data = json.loads(result[0]["text"])
        assert data["disputed"] is True
        assert data["dispute_reason"] == "Incorrect amount"

    def test_mcp_persistence_across_calls(self, ledger):
        """Verify settlement data persists across MCP calls."""
        from agent_ledger.mcp_server import handle_tool_call
        import json

        # Create batch
        result = handle_tool_call(ledger, "create_settlement_batch", {"name": "Persist"})
        batch_id = json.loads(result[0]["text"])["id"]

        # Add item
        handle_tool_call(ledger, "add_settlement_item", {
            "batch_id": batch_id,
            "payer": "a",
            "payee": "b",
            "amount": 100,
        })

        # Retrieve batch — should have the item
        result = handle_tool_call(ledger, "get_settlement_batch", {
            "batch_id": batch_id,
        })
        data = json.loads(result[0]["text"])
        assert len(data["items"]) == 1
        assert data["items"][0]["payer"] == "a"

    def test_mcp_get_proof(self, ledger):
        from agent_ledger.mcp_server import handle_tool_call
        import json

        result = handle_tool_call(ledger, "create_settlement_batch", {})
        batch_id = json.loads(result[0]["text"])["id"]

        handle_tool_call(ledger, "add_settlement_item", {
            "batch_id": batch_id,
            "payer": "a",
            "payee": "b",
            "amount": 100,
        })
        handle_tool_call(ledger, "calculate_settlement_netting", {
            "batch_id": batch_id,
        })

        result = handle_tool_call(ledger, "get_settlement_proof", {
            "batch_id": batch_id,
        })
        data = json.loads(result[0]["text"])
        assert data["proof_hash"]
        assert data["item_count"] == 1
        assert data["total_gross_volume"] == 100.0

    def test_mcp_get_proof_before_calc(self, ledger):
        from agent_ledger.mcp_server import handle_tool_call
        import json

        result = handle_tool_call(ledger, "create_settlement_batch", {})
        batch_id = json.loads(result[0]["text"])["id"]

        result = handle_tool_call(ledger, "get_settlement_proof", {
            "batch_id": batch_id,
        })
        data = json.loads(result[0]["text"])
        assert "error" in data


# ════════════════════════════════════════════════════════════════════
# REALISTIC SCENARIO TESTS
# ════════════════════════════════════════════════════════════════════


class TestScenarios:
    def test_agent_marketplace_scenario(self, engine):
        """Simulate an agent marketplace where multiple agents transact."""
        batch = engine.create_batch("Agent Marketplace — Weekly Settlement")

        # Agent A provides service to Agent B: $500
        engine.add_item(batch.id, "agent_b", "agent_a", 500,
                        item_type=SettlementItemType.SERVICE_FEE,
                        reference="SVC-001")
        # Agent B provides data to Agent C: $300
        engine.add_item(batch.id, "agent_c", "agent_b", 300,
                        item_type=SettlementItemType.SERVICE_FEE,
                        reference="SVC-002")
        # Agent C provides API calls to Agent A: $200
        engine.add_item(batch.id, "agent_a", "agent_c", 200,
                        item_type=SettlementItemType.SERVICE_FEE,
                        reference="SVC-003")
        # Agent A owes commission to marketplace: $50
        engine.add_item(batch.id, "agent_a", "marketplace", 50,
                        item_type=SettlementItemType.COMMISSION,
                        reference="COMM-001")

        result = engine.calculate_netting(batch.id)

        # Positions:
        # agent_a: owes 200+50=250, owed 500 → net +250 (owed)
        # agent_b: owes 500, owed 300 → net -200 (owes)
        # agent_c: owes 300, owed 200 → net -100 (owes)
        # marketplace: owed 50 → net +50 (owed)
        positions = {p.party: p for p in result.net_positions}
        assert positions["agent_a"].net == 250.0
        assert positions["agent_b"].net == -200.0
        assert positions["agent_c"].net == -100.0
        assert positions["marketplace"].net == 50.0

        # Total net payments = sum of debtor obligations = 200 + 100 = 300
        total = sum(p.amount for p in result.net_payments)
        assert total == 300.0

        # Settle
        settled = engine.settle(batch.id)
        assert settled.status == SettlementStatus.SETTLED

    def test_round_amounts(self, engine):
        """Verify amounts are properly rounded."""
        batch = engine.create_batch("Rounding")
        engine.add_item(batch.id, "a", "b", 100.005)
        engine.add_item(batch.id, "b", "a", 33.333)
        result = engine.calculate_netting(batch.id)
        # All amounts should be rounded to 2 decimal places
        for p in result.net_payments:
            assert p.amount == round(p.amount, 2)

    def test_add_after_dispute_resolution(self, engine):
        """After resolving disputes, items can be added and recalculated."""
        batch = engine.create_batch("Dispute Flow")
        item1 = engine.add_item(batch.id, "a", "b", 100)
        item2 = engine.add_item(batch.id, "b", "a", 60)

        # Dispute and resolve
        engine.dispute_item(batch.id, item2.id, "Wrong")
        engine.resolve_dispute(batch.id, item2.id)
        assert batch.status == SettlementStatus.DRAFT

        # Can add new items now
        engine.add_item(batch.id, "c", "a", 30)
        result = engine.calculate_netting(batch.id)
        assert result.status == SettlementStatus.CALCULATED
