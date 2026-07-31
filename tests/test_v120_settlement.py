"""Tests for v1.2.0: Multi-Currency Netting, Fee Engine, Partial Settlement."""

import pytest
import tempfile
from pathlib import Path

from agent_ledger.models import AccountType, ExchangeRate
from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.settlement import (
    SettlementEngine,
    SettlementBatch,
    SettlementItem,
    SettlementItemType,
    SettlementStatus,
    SettlementFee,
    SettlementFeeType,
    FeeAllocation,
    NetPositionDirection,
    NetPosition,
    NetPayment,
    PartialSettlement,
)
from agent_ledger.exceptions import (
    LedgerError,
    InvalidSettlementStateError,
    InvalidFeeError,
    SettlementFeeNotFoundError,
    DuplicateSettlementFeeError,
    SettlementItemNotFoundError,
    PartialSettlementError,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Fresh settlement engine (no FX converter)."""
    return SettlementEngine()


@pytest.fixture
def fx_engine():
    """Settlement engine with a simple FX converter.

    USD is the base. EUR = 1.1 USD, GBP = 1.25 USD, JPY = 0.007 USD.
    """
    rates = {
        ("USD", "EUR"): 1 / 1.1,
        ("EUR", "USD"): 1.1,
        ("USD", "GBP"): 1 / 1.25,
        ("GBP", "USD"): 1.25,
        ("USD", "JPY"): 1 / 0.007,
        ("JPY", "USD"): 0.007,
    }

    def fx_convert(amount: float, from_cur: str, to_cur: str) -> float:
        from_cur = from_cur.upper()
        to_cur = to_cur.upper()
        if from_cur == to_cur:
            return round(amount, 2)
        rate = rates.get((from_cur, to_cur))
        if rate is None:
            raise ValueError(f"No rate for {from_cur}->{to_cur}")
        return round(amount * rate, 2)

    return SettlementEngine(fx_converter=fx_convert)


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


# ════════════════════════════════════════════════════════════════════
# FEE ENUM TESTS
# ════════════════════════════════════════════════════════════════════


class TestFeeEnums:
    def test_fee_type_values(self):
        assert SettlementFeeType.PROCESSING.value == "processing"
        assert SettlementFeeType.NETWORK.value == "network"
        assert SettlementFeeType.GAS.value == "gas"
        assert SettlementFeeType.FX_SPREAD.value == "fx_spread"
        assert SettlementFeeType.LATE.value == "late"
        assert SettlementFeeType.COMMISSION.value == "commission"
        assert SettlementFeeType.CUSTOM.value == "custom"

    def test_fee_allocation_values(self):
        assert FeeAllocation.PAYER.value == "payer"
        assert FeeAllocation.PAYEE.value == "payee"
        assert FeeAllocation.SPLIT.value == "split"

    def test_fee_type_from_string(self):
        assert SettlementFeeType("gas") == SettlementFeeType.GAS

    def test_fee_allocation_from_string(self):
        assert FeeAllocation("split") == FeeAllocation.SPLIT


# ════════════════════════════════════════════════════════════════════
# FEE DATA MODEL TESTS
# ════════════════════════════════════════════════════════════════════


class TestFeeDataModel:
    def test_settlement_fee_defaults(self):
        fee = SettlementFee()
        assert fee.fee_type == SettlementFeeType.PROCESSING
        assert fee.allocation == FeeAllocation.PAYER
        assert fee.amount == 0.0
        assert fee.currency == "USD"
        assert fee.item_id is None
        assert fee.reference == ""
        assert fee.metadata == {}

    def test_settlement_fee_with_values(self):
        fee = SettlementFee(
            fee_type=SettlementFeeType.GAS,
            amount=5.0,
            currency="ETH",
            allocation=FeeAllocation.SPLIT,
            item_id="item-123",
            description="Gas for transaction",
        )
        assert fee.fee_type == SettlementFeeType.GAS
        assert fee.amount == 5.0
        assert fee.currency == "ETH"
        assert fee.allocation == FeeAllocation.SPLIT
        assert fee.item_id == "item-123"

    def test_batch_has_fees_list(self):
        batch = SettlementBatch()
        assert batch.fees == []

    def test_batch_has_partial_settlements_list(self):
        batch = SettlementBatch()
        assert batch.partial_settlements == []


# ════════════════════════════════════════════════════════════════════
# FEE MANAGEMENT TESTS
# ════════════════════════════════════════════════════════════════════


class TestFeeManagement:
    def test_add_fee_basic(self, engine):
        batch = engine.create_batch("Test", currency="USD")
        fee = engine.add_fee(batch.id, amount=10.0)
        assert fee.amount == 10.0
        assert fee.fee_type == SettlementFeeType.PROCESSING
        assert len(batch.fees) == 1

    def test_add_fee_with_type(self, engine):
        batch = engine.create_batch("Test")
        fee = engine.add_fee(batch.id, fee_type=SettlementFeeType.GAS, amount=0.5)
        assert fee.fee_type == SettlementFeeType.GAS

    def test_add_fee_string_type_coerced(self, engine):
        batch = engine.create_batch("Test")
        fee = engine.add_fee(batch.id, fee_type="network", amount=1.0)
        assert fee.fee_type == SettlementFeeType.NETWORK

    def test_add_fee_invalid_type_raises(self, engine):
        batch = engine.create_batch("Test")
        with pytest.raises(ValueError):
            engine.add_fee(batch.id, fee_type="nonexistent", amount=1.0)

    def test_add_fee_zero_amount_raises(self, engine):
        batch = engine.create_batch("Test")
        with pytest.raises(InvalidFeeError):
            engine.add_fee(batch.id, amount=0.0)

    def test_add_fee_negative_amount_raises(self, engine):
        batch = engine.create_batch("Test")
        with pytest.raises(InvalidFeeError):
            engine.add_fee(batch.id, amount=-5.0)

    def test_add_fee_with_allocation(self, engine):
        batch = engine.create_batch("Test")
        fee = engine.add_fee(batch.id, amount=10.0, allocation=FeeAllocation.PAYEE)
        assert fee.allocation == FeeAllocation.PAYEE

    def test_add_fee_string_allocation_coerced(self, engine):
        batch = engine.create_batch("Test")
        fee = engine.add_fee(batch.id, amount=10.0, allocation="split")
        assert fee.allocation == FeeAllocation.SPLIT

    def test_add_fee_linked_to_item(self, engine):
        batch = engine.create_batch("Test")
        item = engine.add_item(batch.id, "a", "b", 100.0)
        fee = engine.add_fee(batch.id, amount=5.0, item_id=item.id)
        assert fee.item_id == item.id

    def test_add_fee_linked_to_nonexistent_item_raises(self, engine):
        batch = engine.create_batch("Test")
        with pytest.raises(SettlementItemNotFoundError):
            engine.add_fee(batch.id, amount=5.0, item_id="nonexistent-id")

    def test_add_fee_duplicate_reference_raises(self, engine):
        batch = engine.create_batch("Test")
        engine.add_fee(batch.id, amount=5.0, reference="FEE-001")
        with pytest.raises(DuplicateSettlementFeeError):
            engine.add_fee(batch.id, amount=10.0, reference="FEE-001")

    def test_add_fee_to_settled_batch_raises(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)
        engine.calculate_netting(batch.id)
        engine.settle(batch.id)
        with pytest.raises(InvalidSettlementStateError):
            engine.add_fee(batch.id, amount=5.0)

    def test_remove_fee(self, engine):
        batch = engine.create_batch("Test")
        fee = engine.add_fee(batch.id, amount=10.0)
        assert len(batch.fees) == 1
        engine.remove_fee(batch.id, fee.id)
        assert len(batch.fees) == 0

    def test_remove_nonexistent_fee_raises(self, engine):
        batch = engine.create_batch("Test")
        with pytest.raises(SettlementFeeNotFoundError):
            engine.remove_fee(batch.id, "nonexistent")

    def test_list_fees_all(self, engine):
        batch = engine.create_batch("Test")
        engine.add_fee(batch.id, amount=10.0)
        engine.add_fee(batch.id, amount=5.0, fee_type=SettlementFeeType.GAS)
        fees = engine.list_fees(batch.id)
        assert len(fees) == 2

    def test_list_fees_filtered_by_item(self, engine):
        batch = engine.create_batch("Test")
        item = engine.add_item(batch.id, "a", "b", 100.0)
        engine.add_fee(batch.id, amount=10.0, item_id=item.id)
        engine.add_fee(batch.id, amount=5.0)  # batch-level fee
        fees = engine.list_fees(batch.id, item_id=item.id)
        assert len(fees) == 1
        assert fees[0].amount == 10.0

    def test_get_total_fees(self, engine):
        batch = engine.create_batch("Test", currency="USD")
        engine.add_fee(batch.id, amount=10.0)
        engine.add_fee(batch.id, amount=5.0)
        assert engine.get_total_fees(batch.id) == 15.0

    def test_get_total_fees_empty(self, engine):
        batch = engine.create_batch("Test")
        assert engine.get_total_fees(batch.id) == 0.0


# ════════════════════════════════════════════════════════════════════
# FEE NETTING INTEGRATION TESTS
# ════════════════════════════════════════════════════════════════════


class TestFeeNettingIntegration:
    def test_payer_fee_increases_payer_obligation(self, engine):
        """When payer bears a $10 fee on a $100 obligation, payer's gross_out = $110."""
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        engine.add_fee(batch.id, amount=10.0, allocation=FeeAllocation.PAYER,
                       item_id=batch.items[0].id)
        engine.calculate_netting(batch.id)

        pos_a = engine.get_party_summary(batch.id, "agent_a")
        pos_b = engine.get_party_summary(batch.id, "agent_b")
        # agent_a owes 100 + 10 fee = 110 gross
        assert pos_a.gross_out == 110.0
        # agent_b is owed 100, but no fee reduction
        assert pos_b.gross_in == 100.0

    def test_payee_fee_decreases_payee_receipt(self, engine):
        """When payee bears a $10 fee on a $100 obligation, payee gross_in = $90."""
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        engine.add_fee(batch.id, amount=10.0, allocation=FeeAllocation.PAYEE,
                       item_id=batch.items[0].id)
        engine.calculate_netting(batch.id)

        pos_b = engine.get_party_summary(batch.id, "agent_b")
        # agent_b is owed 100, but pays 10 fee → receives 90
        assert pos_b.gross_in == 90.0

    def test_split_fee_half_to_each(self, engine):
        """When fee is split $10, payer pays +5, payee receives -5."""
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        engine.add_fee(batch.id, amount=10.0, allocation=FeeAllocation.SPLIT,
                       item_id=batch.items[0].id)
        engine.calculate_netting(batch.id)

        pos_a = engine.get_party_summary(batch.id, "agent_a")
        pos_b = engine.get_party_summary(batch.id, "agent_b")
        assert pos_a.gross_out == 105.0  # 100 + 5
        assert pos_b.gross_in == 95.0    # 100 - 5

    def test_multiple_fees_on_same_item(self, engine):
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        engine.add_fee(batch.id, amount=5.0, allocation=FeeAllocation.PAYER,
                       item_id=batch.items[0].id)
        engine.add_fee(batch.id, amount=3.0, allocation=FeeAllocation.PAYER,
                       item_id=batch.items[0].id)
        engine.calculate_netting(batch.id)

        pos_a = engine.get_party_summary(batch.id, "agent_a")
        assert pos_a.gross_out == 108.0  # 100 + 5 + 3

    def test_fee_on_different_items(self, engine):
        batch = engine.create_batch("Test", currency="USD")
        item1 = engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        item2 = engine.add_item(batch.id, "agent_b", "agent_c", 50.0)
        engine.add_fee(batch.id, amount=10.0, allocation=FeeAllocation.PAYER,
                       item_id=item1.id)
        engine.add_fee(batch.id, amount=5.0, allocation=FeeAllocation.PAYER,
                       item_id=item2.id)
        engine.calculate_netting(batch.id)

        pos_a = engine.get_party_summary(batch.id, "agent_a")
        pos_b = engine.get_party_summary(batch.id, "agent_b")
        pos_c = engine.get_party_summary(batch.id, "agent_c")
        assert pos_a.gross_out == 110.0  # 100 + 10
        assert pos_b.gross_out == 55.0   # 50 + 5

    def test_fee_appears_in_optimization_report(self, engine):
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        engine.add_fee(batch.id, amount=10.0, item_id=batch.items[0].id)
        engine.calculate_netting(batch.id)

        report = engine.get_optimization_report(batch.id)
        assert report["fees"] == 1
        assert report["total_fees"] == 10.0

    def test_batch_level_fee_not_applied_to_parties(self, engine):
        """Fees without item_id should not affect party gross positions."""
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        engine.add_fee(batch.id, amount=10.0)  # no item_id
        engine.calculate_netting(batch.id)

        pos_a = engine.get_party_summary(batch.id, "agent_a")
        assert pos_a.gross_out == 100.0  # unchanged


# ════════════════════════════════════════════════════════════════════
# MULTI-CURRENCY NETTING TESTS
# ════════════════════════════════════════════════════════════════════


class TestMultiCurrencyNetting:
    def test_fx_converter_optional(self, engine):
        """Engine without FX converter works for single-currency items."""
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "a", "b", 100.0)
        result = engine.calculate_netting(batch.id)
        assert result.status == SettlementStatus.CALCULATED

    def test_multi_currency_without_converter_raises(self, engine):
        """Adding items in different currency than batch without converter raises."""
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "a", "b", 100.0, currency="EUR")
        with pytest.raises(LedgerError, match="Multi-currency settlement requires"):
            engine.calculate_netting(batch.id)

    def test_multi_currency_with_converter(self, fx_engine):
        """EUR item gets converted to USD batch currency."""
        batch = fx_engine.create_batch("Test", currency="USD")
        fx_engine.add_item(batch.id, "a", "b", 100.0, currency="EUR")
        result = fx_engine.calculate_netting(batch.id)
        # 100 EUR * 1.1 = 110 USD
        pos_b = fx_engine.get_party_summary(batch.id, "b")
        assert pos_b.gross_in == 110.0

    def test_multi_currency_netting_mixed(self, fx_engine):
        """Mix USD and EUR items in a USD batch."""
        batch = fx_engine.create_batch("Mixed", currency="USD")
        # a owes b 100 USD
        fx_engine.add_item(batch.id, "agent_a", "agent_b", 100.0, currency="USD")
        # b owes a 50 EUR = 55 USD
        fx_engine.add_item(batch.id, "agent_b", "agent_a", 50.0, currency="EUR")
        result = fx_engine.calculate_netting(batch.id)

        # Net: a owes b 100 USD - 55 USD = 45 USD
        pos_a = fx_engine.get_party_summary(batch.id, "agent_a")
        assert pos_a.net == -45.0

    def test_multi_currency_fee_conversion(self, fx_engine):
        """Fee in EUR gets converted to USD batch currency."""
        batch = fx_engine.create_batch("Test", currency="USD")
        item = fx_engine.add_item(batch.id, "a", "b", 100.0, currency="USD")
        # 10 EUR fee * 1.1 = 11 USD
        fx_engine.add_fee(batch.id, amount=10.0, currency="EUR",
                          allocation=FeeAllocation.PAYER, item_id=item.id)
        fx_engine.calculate_netting(batch.id)

        pos_a = fx_engine.get_party_summary(batch.id, "a")
        assert pos_a.gross_out == 111.0  # 100 + 11

    def test_same_currency_item_no_conversion(self, fx_engine):
        """Items in batch currency pass through unchanged."""
        batch = fx_engine.create_batch("Test", currency="USD")
        fx_engine.add_item(batch.id, "a", "b", 100.0, currency="USD")
        fx_engine.calculate_netting(batch.id)

        pos_b = fx_engine.get_party_summary(batch.id, "b")
        assert pos_b.gross_in == 100.0

    def test_converter_callable_signature(self):
        """FX converter is called with (amount, from_cur, to_cur)."""
        calls = []

        def tracker(amount, from_cur, to_cur):
            calls.append((amount, from_cur, to_cur))
            return amount * 2

        engine = SettlementEngine(fx_converter=tracker)
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "a", "b", 50.0, currency="EUR")
        engine.calculate_netting(batch.id)
        assert len(calls) > 0
        assert calls[0] == (50.0, "EUR", "USD")


# ════════════════════════════════════════════════════════════════════
# PARTIAL SETTLEMENT TESTS
# ════════════════════════════════════════════════════════════════════


class TestPartialSettlement:
    @pytest.fixture
    def calculated_batch(self, engine):
        """A batch that has been netted with 2 net payments."""
        batch = engine.create_batch("Test", currency="USD")
        # a owes b 100, c owes b 50
        engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        engine.add_item(batch.id, "agent_c", "agent_b", 50.0)
        engine.calculate_netting(batch.id)
        return batch, engine

    def test_record_partial_settlement(self, calculated_batch):
        batch, engine = calculated_batch
        # net_payments: a->b 100, c->b 50
        ps = engine.record_partial_settlement(batch.id, 0, 40.0)
        assert ps.amount == 40.0
        assert ps.payer == "agent_a"
        assert ps.payee == "agent_b"
        assert len(batch.partial_settlements) == 1

    def test_outstanding_after_partial(self, calculated_batch):
        batch, engine = calculated_batch
        engine.record_partial_settlement(batch.id, 0, 40.0)
        balances = engine.get_outstanding_balances(batch.id)
        # First payment: 100 - 40 = 60 outstanding
        assert balances[0]["outstanding"] == 60.0
        assert balances[0]["paid"] == 40.0
        assert not balances[0]["fully_settled"]

    def test_multiple_partials_same_payment(self, calculated_batch):
        batch, engine = calculated_batch
        engine.record_partial_settlement(batch.id, 0, 40.0)
        engine.record_partial_settlement(batch.id, 0, 30.0)
        balances = engine.get_outstanding_balances(batch.id)
        assert balances[0]["paid"] == 70.0
        assert balances[0]["outstanding"] == 30.0

    def test_fully_settle_via_partials(self, calculated_batch):
        batch, engine = calculated_batch
        engine.record_partial_settlement(batch.id, 0, 60.0)
        engine.record_partial_settlement(batch.id, 0, 40.0)
        engine.record_partial_settlement(batch.id, 1, 50.0)
        assert engine.is_fully_settled(batch.id)
        balances = engine.get_outstanding_balances(batch.id)
        assert all(b["fully_settled"] for b in balances)

    def test_partial_exceeds_outstanding_raises(self, calculated_batch):
        batch, engine = calculated_batch
        engine.record_partial_settlement(batch.id, 0, 60.0)
        with pytest.raises(PartialSettlementError, match="exceeds outstanding"):
            engine.record_partial_settlement(batch.id, 0, 50.0)  # only 40 left

    def test_partial_zero_amount_raises(self, calculated_batch):
        batch, engine = calculated_batch
        with pytest.raises(PartialSettlementError, match="must be positive"):
            engine.record_partial_settlement(batch.id, 0, 0.0)

    def test_partial_negative_amount_raises(self, calculated_batch):
        batch, engine = calculated_batch
        with pytest.raises(PartialSettlementError):
            engine.record_partial_settlement(batch.id, 0, -10.0)

    def test_partial_invalid_index_raises(self, calculated_batch):
        batch, engine = calculated_batch
        with pytest.raises(PartialSettlementError, match="out of range"):
            engine.record_partial_settlement(batch.id, 99, 10.0)

    def test_partial_on_draft_batch_raises(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)
        # No netting yet
        with pytest.raises(PartialSettlementError, match="calculate netting"):
            engine.record_partial_settlement(batch.id, 0, 50.0)

    def test_settle_from_partials_success(self, calculated_batch):
        batch, engine = calculated_batch
        engine.record_partial_settlement(batch.id, 0, 100.0)
        engine.record_partial_settlement(batch.id, 1, 50.0)
        result = engine.settle_from_partials(batch.id)
        assert result.status == SettlementStatus.SETTLED

    def test_settle_from_partials_incomplete_raises(self, calculated_batch):
        batch, engine = calculated_batch
        engine.record_partial_settlement(batch.id, 0, 50.0)
        with pytest.raises(PartialSettlementError, match="outstanding balances"):
            engine.settle_from_partials(batch.id)

    def test_settle_from_partials_wrong_state_raises(self, engine):
        batch = engine.create_batch("Test")
        with pytest.raises(PartialSettlementError):
            engine.settle_from_partials(batch.id)

    def test_is_fully_settled_false_initially(self, calculated_batch):
        batch, engine = calculated_batch
        assert not engine.is_fully_settled(batch.id)

    def test_is_fully_settled_false_empty(self, engine):
        batch = engine.create_batch("Test")
        assert not engine.is_fully_settled(batch.id)

    def test_partial_with_reference(self, calculated_batch):
        batch, engine = calculated_batch
        ps = engine.record_partial_settlement(
            batch.id, 0, 40.0, reference="TXN-0xabc"
        )
        assert ps.reference == "TXN-0xabc"

    def test_partial_with_metadata(self, calculated_batch):
        batch, engine = calculated_batch
        ps = engine.record_partial_settlement(
            batch.id, 0, 40.0, metadata={"chain": "ethereum", "block": 12345}
        )
        assert ps.metadata["chain"] == "ethereum"

    def test_outstanding_empty_batch(self, engine):
        batch = engine.create_batch("Test")
        assert engine.get_outstanding_balances(batch.id) == []

    def test_partial_within_one_cent_tolerance(self, calculated_batch):
        """Allow 1-cent overpayment tolerance."""
        batch, engine = calculated_batch
        # Pay 100.01 for a 100.00 obligation — should not raise
        engine.record_partial_settlement(batch.id, 0, 100.01)
        balances = engine.get_outstanding_balances(batch.id)
        assert balances[0]["fully_settled"]


# ════════════════════════════════════════════════════════════════════
# OPTIMIZATION REPORT TESTS
# ════════════════════════════════════════════════════════════════════


class TestOptimizationReport:
    def test_report_requires_netting(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)
        with pytest.raises(InvalidSettlementStateError, match="No proof"):
            engine.get_optimization_report(batch.id)

    def test_report_basic_fields(self, engine):
        batch = engine.create_batch("Test Batch", currency="USD")
        engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        engine.add_item(batch.id, "agent_b", "agent_a", 60.0)
        engine.calculate_netting(batch.id)

        report = engine.get_optimization_report(batch.id)
        assert report["batch_name"] == "Test Batch"
        assert report["currency"] == "USD"
        assert report["items"] == 2
        assert report["participants"] == 2
        assert report["fees"] == 0
        assert report["net_payments"] == 1  # only a->b 40
        assert report["gross_volume"] == 160.0
        assert report["net_volume"] == 40.0
        assert report["netted_savings"] == 120.0
        assert report["savings_percentage"] == 75.0
        assert report["status"] == "calculated"

    def test_report_savings_percentage(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)
        engine.add_item(batch.id, "b", "a", 100.0)  # fully offset
        engine.calculate_netting(batch.id)

        report = engine.get_optimization_report(batch.id)
        assert report["net_volume"] == 0.0
        assert report["savings_percentage"] == 100.0

    def test_report_includes_fees(self, engine):
        batch = engine.create_batch("Test")
        item = engine.add_item(batch.id, "a", "b", 100.0)
        engine.add_fee(batch.id, amount=10.0, item_id=item.id)
        engine.calculate_netting(batch.id)

        report = engine.get_optimization_report(batch.id)
        assert report["fees"] == 1
        assert report["total_fees"] == 10.0

    def test_report_includes_partials(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)
        engine.calculate_netting(batch.id)
        engine.record_partial_settlement(batch.id, 0, 50.0)

        report = engine.get_optimization_report(batch.id)
        assert report["partial_settlements"] == 1

    def test_report_party_breakdown(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "agent_a", "agent_b", 100.0)
        engine.add_item(batch.id, "agent_b", "agent_a", 60.0)
        engine.calculate_netting(batch.id)

        report = engine.get_optimization_report(batch.id)
        assert len(report["party_breakdown"]) == 2
        a_info = next(p for p in report["party_breakdown"] if p["party"] == "agent_a")
        assert a_info["direction"] == "owes"
        assert a_info["net"] == -40.0

    def test_report_payment_reduction(self, engine):
        batch = engine.create_batch("Test")
        # 4 items, but netting reduces to 1 payment
        engine.add_item(batch.id, "a", "b", 100.0)
        engine.add_item(batch.id, "b", "a", 60.0)
        engine.add_item(batch.id, "c", "a", 30.0)
        engine.add_item(batch.id, "b", "c", 20.0)
        engine.calculate_netting(batch.id)

        report = engine.get_optimization_report(batch.id)
        assert report["items"] == 4
        # Should have reduced payment count
        assert report["net_payments"] < report["items"]

    def test_report_disputed_items(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)
        item2 = engine.add_item(batch.id, "b", "a", 60.0)
        engine.dispute_item(batch.id, item2.id, "Incorrect amount")
        engine.calculate_netting(batch.id)

        report = engine.get_optimization_report(batch.id)
        assert report["disputed_items"] == 1


# ════════════════════════════════════════════════════════════════════
# SERIALIZATION TESTS (fees + partials round-trip)
# ════════════════════════════════════════════════════════════════════


class TestSerializationV12:
    def test_fee_round_trip(self, engine):
        batch = engine.create_batch("Test", currency="USD")
        engine.add_item(batch.id, "a", "b", 100.0)
        engine.add_fee(batch.id, amount=10.0, fee_type=SettlementFeeType.GAS,
                       allocation=FeeAllocation.SPLIT, item_id=batch.items[0].id,
                       reference="FEE-1")

        data = engine.to_dict()

        engine2 = SettlementEngine()
        engine2.from_dict(data)
        batch2 = engine2.get_batch(batch.id)
        assert len(batch2.fees) == 1
        assert batch2.fees[0].amount == 10.0
        assert batch2.fees[0].fee_type == SettlementFeeType.GAS
        assert batch2.fees[0].allocation == FeeAllocation.SPLIT
        assert batch2.fees[0].reference == "FEE-1"

    def test_partial_settlement_round_trip(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)
        engine.calculate_netting(batch.id)
        engine.record_partial_settlement(batch.id, 0, 50.0, reference="TXN-1")

        data = engine.to_dict()

        engine2 = SettlementEngine()
        engine2.from_dict(data)
        batch2 = engine2.get_batch(batch.id)
        assert len(batch2.partial_settlements) == 1
        assert batch2.partial_settlements[0].amount == 50.0
        assert batch2.partial_settlements[0].reference == "TXN-1"

    def test_empty_fees_partials_round_trip(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)

        data = engine.to_dict()
        assert "fees" in data["batches"][0]
        assert "partial_settlements" in data["batches"][0]
        assert data["batches"][0]["fees"] == []

    def test_multi_fee_round_trip(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)
        engine.add_fee(batch.id, amount=5.0)
        engine.add_fee(batch.id, amount=10.0, fee_type=SettlementFeeType.NETWORK)

        data = engine.to_dict()
        engine2 = SettlementEngine()
        engine2.from_dict(data)
        batch2 = engine2.get_batch(batch.id)
        assert len(batch2.fees) == 2


# ════════════════════════════════════════════════════════════════════
# MCP SERVER INTEGRATION TESTS
# ════════════════════════════════════════════════════════════════════


class TestMCPServerV12:
    @pytest.fixture
    def initialized_ledger(self, ledger):
        """Ledger with FX rates configured."""
        ledger.add_exchange_rate("USD", "EUR", 1 / 1.1)
        ledger.add_exchange_rate("EUR", "USD", 1.1)
        return ledger

    @staticmethod
    def _call(ledger, tool, args):
        """Helper: call MCP tool and parse JSON from content block."""
        import json
        from agent_ledger.mcp_server import handle_tool_call
        result = handle_tool_call(ledger, tool, args)
        return json.loads(result[0]["text"])

    def test_add_settlement_fee_tool(self, initialized_ledger):
        batch_data = self._call(initialized_ledger, "create_settlement_batch", {
            "name": "Test", "currency": "USD"
        })
        batch_id = batch_data["id"]

        item_data = self._call(initialized_ledger, "add_settlement_item", {
            "batch_id": batch_id, "payer": "a", "payee": "b", "amount": 100.0
        })

        fee_data = self._call(initialized_ledger, "add_settlement_fee", {
            "batch_id": batch_id, "amount": 10.0, "fee_type": "gas",
            "allocation": "payer", "item_id": item_data["id"]
        })
        assert fee_data["amount"] == 10.0
        assert fee_data["fee_type"] == "gas"

    def test_list_settlement_fees_tool(self, initialized_ledger):
        batch_data = self._call(initialized_ledger, "create_settlement_batch", {
            "name": "Test"
        })
        batch_id = batch_data["id"]
        self._call(initialized_ledger, "add_settlement_fee", {
            "batch_id": batch_id, "amount": 10.0
        })
        self._call(initialized_ledger, "add_settlement_fee", {
            "batch_id": batch_id, "amount": 5.0, "fee_type": "network"
        })

        fees = self._call(initialized_ledger, "list_settlement_fees", {
            "batch_id": batch_id
        })
        assert len(fees) == 2

    def test_remove_settlement_fee_tool(self, initialized_ledger):
        batch_data = self._call(initialized_ledger, "create_settlement_batch", {
            "name": "Test"
        })
        batch_id = batch_data["id"]
        fee = self._call(initialized_ledger, "add_settlement_fee", {
            "batch_id": batch_id, "amount": 10.0
        })

        result = self._call(initialized_ledger, "remove_settlement_fee", {
            "batch_id": batch_id, "fee_id": fee["id"]
        })
        assert result["removed"] is True

    def test_partial_settlement_tool(self, initialized_ledger):
        batch_data = self._call(initialized_ledger, "create_settlement_batch", {
            "name": "Test"
        })
        batch_id = batch_data["id"]
        self._call(initialized_ledger, "add_settlement_item", {
            "batch_id": batch_id, "payer": "a", "payee": "b", "amount": 100.0
        })
        self._call(initialized_ledger, "calculate_settlement_netting", {
            "batch_id": batch_id
        })

        ps = self._call(initialized_ledger, "record_partial_settlement", {
            "batch_id": batch_id, "net_payment_index": 0, "amount": 40.0
        })
        assert ps["amount"] == 40.0

        balances = self._call(initialized_ledger, "get_outstanding_balances", {
            "batch_id": batch_id
        })
        assert balances[0]["outstanding"] == 60.0

    def test_settle_from_partials_tool(self, initialized_ledger):
        batch_data = self._call(initialized_ledger, "create_settlement_batch", {
            "name": "Test"
        })
        batch_id = batch_data["id"]
        self._call(initialized_ledger, "add_settlement_item", {
            "batch_id": batch_id, "payer": "a", "payee": "b", "amount": 100.0
        })
        self._call(initialized_ledger, "calculate_settlement_netting", {
            "batch_id": batch_id
        })
        self._call(initialized_ledger, "record_partial_settlement", {
            "batch_id": batch_id, "net_payment_index": 0, "amount": 100.0
        })

        result = self._call(initialized_ledger, "settle_from_partials", {
            "batch_id": batch_id
        })
        assert result["status"] == "settled"

    def test_optimization_report_tool(self, initialized_ledger):
        batch_data = self._call(initialized_ledger, "create_settlement_batch", {
            "name": "Opt Test"
        })
        batch_id = batch_data["id"]
        self._call(initialized_ledger, "add_settlement_item", {
            "batch_id": batch_id, "payer": "a", "payee": "b", "amount": 100.0
        })
        self._call(initialized_ledger, "add_settlement_item", {
            "batch_id": batch_id, "payer": "b", "payee": "a", "amount": 60.0
        })
        self._call(initialized_ledger, "calculate_settlement_netting", {
            "batch_id": batch_id
        })

        report = self._call(initialized_ledger, "get_settlement_optimization_report", {
            "batch_id": batch_id
        })
        assert report["gross_volume"] == 160.0
        assert report["net_volume"] == 40.0
        assert report["savings_percentage"] == 75.0

    def test_mcp_fx_integration(self, initialized_ledger):
        """MCP server auto-wires FX converter from ledger exchange rates."""
        batch_data = self._call(initialized_ledger, "create_settlement_batch", {
            "name": "FX Test", "currency": "USD"
        })
        batch_id = batch_data["id"]

        self._call(initialized_ledger, "add_settlement_item", {
            "batch_id": batch_id, "payer": "a", "payee": "b",
            "amount": 100.0
        })
        self._call(initialized_ledger, "add_settlement_item", {
            "batch_id": batch_id, "payer": "b", "payee": "a",
            "amount": 50.0, "currency": "EUR"  # 50 EUR = 55 USD
        })

        result = self._call(initialized_ledger, "calculate_settlement_netting", {
            "batch_id": batch_id
        })
        net_payments = result["net_payments"]
        assert len(net_payments) >= 1

    def test_new_tools_in_tools_list(self):
        """All v1.2 tools are registered in the TOOLS list."""
        from agent_ledger.mcp_server import TOOLS
        tool_names = {t["name"] for t in TOOLS}
        assert "add_settlement_fee" in tool_names
        assert "list_settlement_fees" in tool_names
        assert "remove_settlement_fee" in tool_names
        assert "record_partial_settlement" in tool_names
        assert "get_outstanding_balances" in tool_names
        assert "settle_from_partials" in tool_names
        assert "get_settlement_optimization_report" in tool_names

    def test_total_tool_count(self):
        """Verify settlement tool count increased with v1.2 additions."""
        from agent_ledger.mcp_server import TOOLS
        settlement_tools = [t for t in TOOLS if "settlement" in t["name"].lower()
                           or "partial" in t["name"].lower()
                           or "outstanding" in t["name"].lower()
                           or "optimization" in t["name"].lower()]
        assert len(settlement_tools) >= 14  # 7 original + 7 new


# ════════════════════════════════════════════════════════════════════
# EDGE CASE TESTS
# ════════════════════════════════════════════════════════════════════


class TestEdgeCasesV12:
    def test_fee_then_add_item_resets_disputed(self, engine):
        """Adding items after disputes should work with fees present."""
        batch = engine.create_batch("Test")
        item = engine.add_item(batch.id, "a", "b", 100.0)
        engine.add_fee(batch.id, amount=5.0, item_id=item.id)
        engine.dispute_item(batch.id, item.id, "Wrong")
        assert batch.status == SettlementStatus.DISPUTED
        # Can add another fee while disputed
        engine.add_fee(batch.id, amount=3.0, item_id=item.id)

    def test_convert_identity_same_currency(self, engine):
        """_convert_to_batch_currency returns same amount for same currency."""
        result = engine._convert_to_batch_currency(100.0, "USD", "USD")
        assert result == 100.0

    def test_convert_without_converter_raises(self, engine):
        """No converter + different currency = error."""
        with pytest.raises(LedgerError):
            engine._convert_to_batch_currency(100.0, "EUR", "USD")

    def test_partial_settlement_id_unique(self, engine):
        batch = engine.create_batch("Test")
        engine.add_item(batch.id, "a", "b", 100.0)
        engine.calculate_netting(batch.id)
        ps1 = engine.record_partial_settlement(batch.id, 0, 30.0)
        ps2 = engine.record_partial_settlement(batch.id, 0, 30.0)
        assert ps1.id != ps2.id

    def test_fee_id_unique(self, engine):
        batch = engine.create_batch("Test")
        fee1 = engine.add_fee(batch.id, amount=5.0)
        fee2 = engine.add_fee(batch.id, amount=5.0)
        assert fee1.id != fee2.id

    def test_optimization_report_zero_gross(self, engine):
        """Report handles zero gross volume gracefully."""
        batch = engine.create_batch("Test")
        # Two items that fully offset
        engine.add_item(batch.id, "a", "b", 50.0)
        engine.add_item(batch.id, "b", "a", 50.0)
        engine.calculate_netting(batch.id)
        report = engine.get_optimization_report(batch.id)
        # gross = 100, net = 0 → 100% savings
        assert report["savings_percentage"] == 100.0
