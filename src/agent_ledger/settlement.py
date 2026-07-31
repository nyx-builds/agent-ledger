"""Multi-party settlement & netting engine for agent-ledger.

Enables autonomous agents to batch inter-agent transactions, calculate net
positions via multi-lateral netting, and produce verifiable settlement proofs.
Inspired by clearing-house netting but designed for the agentic economy.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Callable

from .exceptions import (
    LedgerError,
    SettlementNotFoundError,
    SettlementItemNotFoundError,
    InvalidSettlementStateError,
    DuplicateSettlementItemError,
    SettlementFeeNotFoundError,
    DuplicateSettlementFeeError,
    InvalidFeeError,
    PartialSettlementError,
)


class SettlementStatus(str, Enum):
    """Lifecycle states for a settlement batch."""
    DRAFT = "draft"
    CALCULATED = "calculated"       # netting has been computed
    SETTLED = "settled"             # all net payments confirmed
    DISPUTED = "disputed"           # at least one item disputed
    CANCELLED = "cancelled"


class SettlementItemType(str, Enum):
    """What kind of obligation the settlement item represents."""
    INVOICE = "invoice"
    LOAN = "loan"
    SERVICE_FEE = "service_fee"
    REFUND = "refund"
    COMMISSION = "commission"
    CUSTOM = "custom"


class NetPositionDirection(str, Enum):
    """Whether a party owes (pays) or is owed (receives) after netting."""
    OWES = "owes"        # net debtor — must pay
    OWED = "owed"         # net creditor — will receive
    EVEN = "even"          # net zero


class SettlementFeeType(str, Enum):
    """Types of fees that can be attached to settlement items."""
    PROCESSING = "processing"     # platform/handling fee
    NETWORK = "network"           # blockchain/network fee
    GAS = "gas"                   # gas fee (crypto settlements)
    FX_SPREAD = "fx_spread"       # currency conversion spread
    LATE = "late"                 # late settlement penalty
    COMMISSION = "commission"     # commission/intermediary fee
    CUSTOM = "custom"


class FeeAllocation(str, Enum):
    """Who bears the fee cost."""
    PAYER = "payer"       # payer pays extra on top of obligation
    PAYEE = "payee"       # payee absorbs from obligation
    SPLIT = "split"       # 50/50 split between payer and payee


@dataclass
class SettlementItem:
    """A single obligation between two agents within a settlement batch."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    payer: str = ""                              # agent/entity that owes
    payee: str = ""                              # agent/entity that is owed
    amount: float = 0.0
    currency: str = "USD"
    item_type: SettlementItemType = SettlementItemType.CUSTOM
    description: str = ""
    reference: str = ""                           # external invoice/txn ID
    metadata: dict = field(default_factory=dict)
    disputed: bool = False
    dispute_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class NetPosition:
    """The net position of a single party after multi-lateral netting."""
    party: str = ""
    gross_out: float = 0.0     # total this party owes others
    gross_in: float = 0.0      # total others owe this party
    net: float = 0.0           # positive = owed, negative = owes
    direction: NetPositionDirection = NetPositionDirection.EVEN
    currency: str = "USD"


@dataclass
class NetPayment:
    """A single netted payment instruction: payer → payee."""
    payer: str = ""
    payee: str = ""
    amount: float = 0.0
    currency: str = "USD"


@dataclass
class SettlementFee:
    """A fee attached to a settlement item or batch.

    Fees are converted to the batch settlement currency and added to
    the gross obligation volume during netting.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fee_type: SettlementFeeType = SettlementFeeType.PROCESSING
    amount: float = 0.0
    currency: str = "USD"
    description: str = ""
    allocation: FeeAllocation = FeeAllocation.PAYER
    reference: str = ""                     # external reference for dedup
    metadata: dict = field(default_factory=dict)
    item_id: Optional[str] = None           # link to a specific SettlementItem
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PartialSettlement:
    """Records a partial payment of a net obligation.

    Allows agents to settle portions of their net position over time.
    The outstanding balance is tracked until fully settled.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    net_payment_index: int = 0      # index into batch.net_payments
    payer: str = ""
    payee: str = ""
    amount: float = 0.0             # amount paid in this partial settlement
    currency: str = "USD"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reference: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class SettlementProof:
    """Cryptographic proof of a settlement for verification."""
    settlement_id: str = ""
    proof_hash: str = ""
    item_count: int = 0
    total_gross_volume: float = 0.0
    total_net_volume: float = 0.0
    netted_savings: float = 0.0          # gross - net = liquidity saved
    participant_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    settled_at: Optional[datetime] = None


@dataclass
class SettlementBatch:
    """A batch of inter-agent obligations to be netted and settled together."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    currency: str = "USD"
    items: list[SettlementItem] = field(default_factory=list)
    fees: list[SettlementFee] = field(default_factory=list)
    net_positions: list[NetPosition] = field(default_factory=list)
    net_payments: list[NetPayment] = field(default_factory=list)
    partial_settlements: list[PartialSettlement] = field(default_factory=list)
    proof: Optional[SettlementProof] = None
    status: SettlementStatus = SettlementStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    settled_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


class SettlementEngine:
    """Engine for managing settlement batches, netting, and proofs.

    Usage:
        engine = SettlementEngine()
        batch = engine.create_batch("Weekly agent netting", currency="USD")
        engine.add_item(batch, payer="agent_a", payee="agent_b", amount=100)
        engine.add_item(batch, payer="agent_b", payee="agent_a", amount=60)
        result = engine.calculate_netting(batch)
        # agent_a owes agent_b net $40 (100 - 60)
        engine.settle(batch)
    """

    def __init__(self, fx_converter: Optional[Callable] = None):
        """Initialize the settlement engine.

        Args:
            fx_converter: Optional callable ``(amount, from_currency, to_currency) -> float``
                          used to convert multi-currency items and fees to the batch
                          settlement currency before netting. If None, multi-currency
                          items must already be in the batch currency or a
                          CurrencyMismatchError-like LedgerError is raised.
        """
        self._batches: dict[str, SettlementBatch] = {}
        self._fx_converter = fx_converter

    # ── Batch lifecycle ──────────────────────────────────────────

    def create_batch(
        self,
        name: str = "",
        description: str = "",
        currency: str = "USD",
        metadata: Optional[dict] = None,
    ) -> SettlementBatch:
        """Create a new settlement batch."""
        batch = SettlementBatch(
            name=name or f"Settlement-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            description=description,
            currency=currency,
            metadata=metadata or {},
        )
        self._batches[batch.id] = batch
        return batch

    def get_batch(self, batch_id: str) -> SettlementBatch:
        """Retrieve a batch by ID."""
        if batch_id not in self._batches:
            raise SettlementNotFoundError(f"Settlement batch {batch_id} not found")
        return self._batches[batch_id]

    def list_batches(
        self,
        status: Optional[SettlementStatus] = None,
    ) -> list[SettlementBatch]:
        """List settlement batches, optionally filtered by status."""
        batches = list(self._batches.values())
        if status is not None:
            batches = [b for b in batches if b.status == status]
        batches.sort(key=lambda b: b.created_at, reverse=True)
        return batches

    def cancel_batch(self, batch_id: str) -> SettlementBatch:
        """Cancel a settlement batch (only allowed from DRAFT or CALCULATED)."""
        batch = self.get_batch(batch_id)
        if batch.status in (SettlementStatus.SETTLED, SettlementStatus.CANCELLED):
            raise InvalidSettlementStateError(
                f"Cannot cancel batch in {batch.status.value} state"
            )
        batch.status = SettlementStatus.CANCELLED
        return batch

    def delete_batch(self, batch_id: str) -> None:
        """Permanently remove a settlement batch (must be DRAFT or CANCELLED)."""
        batch = self.get_batch(batch_id)
        if batch.status in (SettlementStatus.CALCULATED, SettlementStatus.SETTLED, SettlementStatus.DISPUTED):
            raise InvalidSettlementStateError(
                f"Cannot delete batch in {batch.status.value} state — cancel first"
            )
        del self._batches[batch_id]

    # ── FX conversion ────────────────────────────────────────────

    def _convert_to_batch_currency(self, amount: float, from_currency: str,
                                   to_currency: str) -> float:
        """Convert an amount to the batch settlement currency.

        Uses the FX converter if one was provided; otherwise the amount
        must already be in the target currency.
        """
        if from_currency.upper() == to_currency.upper():
            return round(amount, 2)
        if self._fx_converter is None:
            raise LedgerError(
                f"Multi-currency settlement requires an FX converter. "
                f"Item currency {from_currency} != batch currency {to_currency}. "
                f"Pass fx_converter to SettlementEngine()."
            )
        return round(self._fx_converter(amount, from_currency, to_currency), 2)

    # ── Item management ──────────────────────────────────────────

    def add_item(
        self,
        batch_id: str,
        payer: str,
        payee: str,
        amount: float,
        item_type: SettlementItemType = SettlementItemType.CUSTOM,
        description: str = "",
        reference: str = "",
        currency: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SettlementItem:
        """Add an obligation to a settlement batch."""
        batch = self.get_batch(batch_id)
        if batch.status not in (SettlementStatus.DRAFT, SettlementStatus.DISPUTED):
            raise InvalidSettlementStateError(
                f"Cannot add items to batch in {batch.status.value} state"
            )
        if amount <= 0:
            raise LedgerError("Settlement item amount must be positive")
        if not payer or not payee:
            raise LedgerError("Payer and payee must be specified")
        if payer == payee:
            raise LedgerError("Payer and payee must be different parties")

        # Check for duplicate references
        if reference:
            for item in batch.items:
                if item.reference and item.reference == reference:
                    raise DuplicateSettlementItemError(
                        f"Item with reference '{reference}' already exists in batch"
                    )

        item = SettlementItem(
            payer=payer.strip().lower(),
            payee=payee.strip().lower(),
            amount=round(amount, 2),
            currency=currency or batch.currency,
            item_type=item_type,
            description=description,
            reference=reference,
            metadata=metadata or {},
        )
        batch.items.append(item)

        # If batch was disputed and we resolved disputes, allow recalculation
        if batch.status == SettlementStatus.DISPUTED:
            # Adding items puts it back to draft for recalculation
            batch.status = SettlementStatus.DRAFT
            batch.net_positions = []
            batch.net_payments = []
            batch.proof = None

        return item

    def remove_item(self, batch_id: str, item_id: str) -> SettlementBatch:
        """Remove an item from a settlement batch."""
        batch = self.get_batch(batch_id)
        if batch.status not in (SettlementStatus.DRAFT, SettlementStatus.DISPUTED):
            raise InvalidSettlementStateError(
                f"Cannot remove items from batch in {batch.status.value} state"
            )
        original_len = len(batch.items)
        batch.items = [i for i in batch.items if i.id != item_id]
        if len(batch.items) == original_len:
            raise SettlementItemNotFoundError(f"Item {item_id} not found in batch")

        if batch.status == SettlementStatus.DISPUTED:
            batch.status = SettlementStatus.DRAFT
            batch.net_positions = []
            batch.net_payments = []
            batch.proof = None

        return batch

    def dispute_item(
        self,
        batch_id: str,
        item_id: str,
        reason: str,
    ) -> SettlementItem:
        """Mark a settlement item as disputed."""
        batch = self.get_batch(batch_id)
        if batch.status == SettlementStatus.SETTLED:
            raise InvalidSettlementStateError("Cannot dispute items in a settled batch")
        if batch.status == SettlementStatus.CANCELLED:
            raise InvalidSettlementStateError("Cannot dispute items in a cancelled batch")

        for item in batch.items:
            if item.id == item_id:
                item.disputed = True
                item.dispute_reason = reason
                batch.status = SettlementStatus.DISPUTED
                return item
        raise SettlementItemNotFoundError(f"Item {item_id} not found in batch")

    def resolve_dispute(self, batch_id: str, item_id: str) -> SettlementItem:
        """Resolve a disputed item (marks it as no longer disputed)."""
        batch = self.get_batch(batch_id)
        for item in batch.items:
            if item.id == item_id:
                item.disputed = False
                item.dispute_reason = ""
                # Check if any items remain disputed
                any_disputed = any(i.disputed for i in batch.items)
                if not any_disputed and batch.status == SettlementStatus.DISPUTED:
                    batch.status = SettlementStatus.DRAFT
                return item
        raise SettlementItemNotFoundError(f"Item {item_id} not found in batch")

    # ── Netting engine ───────────────────────────────────────────

    def calculate_netting(self, batch_id: str) -> SettlementBatch:
        """Calculate multi-lateral netting for a settlement batch.

        For each pair of agents, netting reduces gross obligations to the
        minimum settlement payments. E.g. if A owes B $100 and B owes A $60,
        the net is A pays B $40 — saving $120 in gross settlement volume.

        Uses a greedy settling algorithm to minimize the number of payments.
        """
        batch = self.get_batch(batch_id)
        if not batch.items:
            raise InvalidSettlementStateError("Cannot calculate netting on empty batch")
        if batch.status == SettlementStatus.SETTLED:
            raise InvalidSettlementStateError("Batch already settled")
        if batch.status == SettlementStatus.CANCELLED:
            raise InvalidSettlementStateError("Cannot calculate netting on cancelled batch")

        # Exclude disputed items from netting
        active_items = [i for i in batch.items if not i.disputed]
        if not active_items:
            raise InvalidSettlementStateError("All items are disputed — cannot calculate netting")

        # Step 1: Compute bilateral net positions
        # bilateral[payer][payee] = net amount payer owes payee (in batch currency)
        bilateral: dict[str, dict[str, float]] = {}

        for item in active_items:
            payer = item.payer
            payee = item.payee
            if payer not in bilateral:
                bilateral[payer] = {}
            if payee not in bilateral:
                bilateral[payee] = {}

            # Multi-currency: convert item amount to batch currency
            converted = self._convert_to_batch_currency(
                item.amount, item.currency, batch.currency
            )

            # If payee also owes payer, net them
            reverse = bilateral.get(payee, {}).get(payer, 0.0)
            if reverse > 0:
                # Net: reduce both
                netted = min(reverse, converted)
                bilateral[payee][payer] = round(reverse - netted, 2)
                remaining = round(converted - netted, 2)
                if remaining > 0:
                    bilateral[payer][payee] = bilateral.get(payer, {}).get(payee, 0.0) + remaining
            else:
                bilateral[payer][payee] = bilateral.get(payer, {}).get(payee, 0.0) + converted

        # Clean up zero balances
        for payer in list(bilateral.keys()):
            for payee in list(bilateral[payer].keys()):
                if bilateral[payer][payee] <= 0:
                    del bilateral[payer][payee]
            if not bilateral[payer]:
                del bilateral[payer]

        # Step 2: Compute net positions for each party
        # Track ALL parties from active items (not just bilateral survivors)
        all_parties = set()
        gross_out: dict[str, float] = {}  # total a party owes (from active items)
        gross_in: dict[str, float] = {}   # total owed to a party (from active items)

        for item in active_items:
            all_parties.add(item.payer)
            all_parties.add(item.payee)
            converted = self._convert_to_batch_currency(
                item.amount, item.currency, batch.currency
            )
            gross_out[item.payer] = gross_out.get(item.payer, 0.0) + converted
            gross_in[item.payee] = gross_in.get(item.payee, 0.0) + converted

        # Apply fees to gross positions based on allocation
        for fee in batch.fees:
            fee_converted = self._convert_to_batch_currency(
                fee.amount, fee.currency, batch.currency
            )
            if fee.item_id:
                # Find the item this fee belongs to
                linked_item = None
                for item in active_items:
                    if item.id == fee.item_id:
                        linked_item = item
                        break
                if linked_item is None:
                    continue
                payer = linked_item.payer
                payee = linked_item.payee
            else:
                # Batch-level fee — skip party assignment, just add to totals
                continue

            if fee.allocation == FeeAllocation.PAYER:
                # Payer bears the fee — increases their gross obligation
                gross_out[payer] = gross_out.get(payer, 0.0) + fee_converted
            elif fee.allocation == FeeAllocation.PAYEE:
                # Payee bears the fee — reduces what they receive
                gross_in[payee] = gross_in.get(payee, 0.0) - fee_converted
            elif fee.allocation == FeeAllocation.SPLIT:
                half = round(fee_converted / 2, 2)
                gross_out[payer] = gross_out.get(payer, 0.0) + half
                gross_in[payee] = gross_in.get(payee, 0.0) - half

        net_positions: list[NetPosition] = []
        for party in sorted(all_parties):
            go = round(gross_out.get(party, 0.0), 2)
            gi = round(gross_in.get(party, 0.0), 2)
            net = round(gi - go, 2)  # positive = owed
            if net > 0:
                direction = NetPositionDirection.OWED
            elif net < 0:
                direction = NetPositionDirection.OWES
            else:
                direction = NetPositionDirection.EVEN
            net_positions.append(NetPosition(
                party=party,
                gross_out=go,
                gross_in=gi,
                net=net,
                direction=direction,
                currency=batch.currency,
            ))

        batch.net_positions = net_positions

        # Step 3: Compute minimal set of net payments
        # Collect debtors (owe) and creditors (owed)
        # Use greedy: largest debtor pays largest creditor
        debtors = sorted(
            [p for p in net_positions if p.direction == NetPositionDirection.OWES],
            key=lambda p: p.net,  # most negative first
        )
        creditors = sorted(
            [p for p in net_positions if p.direction == NetPositionDirection.OWED],
            key=lambda p: p.net,
            reverse=True,  # most positive first
        )

        net_payments: list[NetPayment] = []
        di = 0  # debtor index
        ci = 0  # creditor index
        debtor_remaining = abs(debtors[di].net) if debtors else 0
        creditor_remaining = creditors[ci].net if creditors else 0

        while di < len(debtors) and ci < len(creditors):
            if debtor_remaining <= 0.001:
                di += 1
                if di < len(debtors):
                    debtor_remaining = abs(debtors[di].net)
                continue
            if creditor_remaining <= 0.001:
                ci += 1
                if ci < len(creditors):
                    creditor_remaining = creditors[ci].net
                continue

            payment = min(debtor_remaining, creditor_remaining)
            payment = round(payment, 2)
            if payment > 0:
                net_payments.append(NetPayment(
                    payer=debtors[di].party,
                    payee=creditors[ci].party,
                    amount=payment,
                    currency=batch.currency,
                ))

            debtor_remaining = round(debtor_remaining - payment, 2)
            creditor_remaining = round(creditor_remaining - payment, 2)

            if debtor_remaining <= 0.001:
                di += 1
                if di < len(debtors):
                    debtor_remaining = abs(debtors[di].net)
            if creditor_remaining <= 0.001:
                ci += 1
                if ci < len(creditors):
                    creditor_remaining = creditors[ci].net

        batch.net_payments = net_payments

        # Step 4: Generate proof
        total_gross = round(sum(gross_out.values()), 2)
        total_net = round(sum(p.amount for p in net_payments), 2)
        proof = SettlementProof(
            settlement_id=batch.id,
            item_count=len(active_items),
            total_gross_volume=total_gross,
            total_net_volume=total_net,
            netted_savings=round(total_gross - total_net, 2),
            participant_count=len(all_parties),
        )
        proof.proof_hash = self._compute_proof_hash(batch, proof)
        batch.proof = proof

        if any(i.disputed for i in batch.items):
            batch.status = SettlementStatus.DISPUTED
        else:
            batch.status = SettlementStatus.CALCULATED

        return batch

    def settle(self, batch_id: str) -> SettlementBatch:
        """Mark a settlement batch as settled.

        Requires the batch to be in CALCULATED or DISPUTED state
        (disputed items are excluded from settlement).
        """
        batch = self.get_batch(batch_id)
        if batch.status not in (SettlementStatus.CALCULATED, SettlementStatus.DISPUTED):
            raise InvalidSettlementStateError(
                f"Must calculate netting before settling (current: {batch.status.value})"
            )
        if not batch.proof:
            raise InvalidSettlementStateError("No proof — run calculate_netting first")

        batch.status = SettlementStatus.SETTLED
        batch.settled_at = datetime.now(timezone.utc)
        batch.proof.settled_at = batch.settled_at
        # Recompute proof hash to include settlement timestamp
        batch.proof.proof_hash = self._compute_proof_hash(batch, batch.proof)
        return batch

    # ── Queries ──────────────────────────────────────────────────

    def get_party_summary(self, batch_id: str, party: str) -> NetPosition:
        """Get the net position for a specific party in a batch."""
        batch = self.get_batch(batch_id)
        party = party.strip().lower()
        for pos in batch.net_positions:
            if pos.party == party:
                return pos
        # If not in net positions, compute from items
        gross_out = sum(i.amount for i in batch.items if i.payer == party and not i.disputed)
        gross_in = sum(i.amount for i in batch.items if i.payee == party and not i.disputed)
        net = round(gross_in - gross_out, 2)
        direction = (
            NetPositionDirection.OWED if net > 0
            else NetPositionDirection.OWES if net < 0
            else NetPositionDirection.EVEN
        )
        return NetPosition(
            party=party,
            gross_out=round(gross_out, 2),
            gross_in=round(gross_in, 2),
            net=net,
            direction=direction,
            currency=batch.currency,
        )

    def get_disputed_items(self, batch_id: str) -> list[SettlementItem]:
        """Get all disputed items in a batch."""
        batch = self.get_batch(batch_id)
        return [i for i in batch.items if i.disputed]

    def verify_proof(self, batch_id: str, expected_hash: str) -> bool:
        """Verify that a settlement proof hash matches."""
        batch = self.get_batch(batch_id)
        if not batch.proof:
            return False
        return batch.proof.proof_hash == expected_hash

    # ── Fee management ───────────────────────────────────────────

    def add_fee(
        self,
        batch_id: str,
        fee_type: SettlementFeeType = SettlementFeeType.PROCESSING,
        amount: float = 0.0,
        currency: Optional[str] = None,
        description: str = "",
        allocation: FeeAllocation = FeeAllocation.PAYER,
        item_id: Optional[str] = None,
        reference: str = "",
        metadata: Optional[dict] = None,
    ) -> SettlementFee:
        """Add a fee to a settlement batch.

        Fees are converted to the batch currency during netting and affect
        gross positions based on the allocation strategy:

        - PAYER: payer's gross obligation increases by the fee amount
        - PAYEE: payee's gross receivable decreases by the fee amount
        - SPLIT: half to payer, half to payee
        """
        batch = self.get_batch(batch_id)
        if batch.status not in (SettlementStatus.DRAFT, SettlementStatus.DISPUTED):
            raise InvalidSettlementStateError(
                f"Cannot add fees to batch in {batch.status.value} state"
            )
        if amount <= 0:
            raise InvalidFeeError("Fee amount must be positive")
        if not isinstance(fee_type, SettlementFeeType):
            fee_type = SettlementFeeType(fee_type)
        if not isinstance(allocation, FeeAllocation):
            allocation = FeeAllocation(allocation)

        # Validate item_id if provided
        if item_id:
            found = any(i.id == item_id for i in batch.items)
            if not found:
                raise SettlementItemNotFoundError(
                    f"Cannot attach fee: item {item_id} not found in batch"
                )

        # Check for duplicate references
        if reference:
            for fee in batch.fees:
                if fee.reference and fee.reference == reference:
                    raise DuplicateSettlementFeeError(
                        f"Fee with reference '{reference}' already exists in batch"
                    )

        fee = SettlementFee(
            fee_type=fee_type,
            amount=round(amount, 2),
            currency=currency or batch.currency,
            description=description,
            allocation=allocation,
            item_id=item_id,
            reference=reference,
            metadata=metadata or {},
        )
        batch.fees.append(fee)
        return fee

    def remove_fee(self, batch_id: str, fee_id: str) -> SettlementBatch:
        """Remove a fee from a batch."""
        batch = self.get_batch(batch_id)
        if batch.status not in (SettlementStatus.DRAFT, SettlementStatus.DISPUTED):
            raise InvalidSettlementStateError(
                f"Cannot remove fees from batch in {batch.status.value} state"
            )
        for i, fee in enumerate(batch.fees):
            if fee.id == fee_id:
                batch.fees.pop(i)
                return batch
        raise SettlementFeeNotFoundError(f"Fee {fee_id} not found in batch")

    def list_fees(self, batch_id: str, item_id: Optional[str] = None) -> list[SettlementFee]:
        """List fees in a batch, optionally filtered by linked item."""
        batch = self.get_batch(batch_id)
        if item_id:
            return [f for f in batch.fees if f.item_id == item_id]
        return list(batch.fees)

    def get_total_fees(self, batch_id: str) -> float:
        """Get the total fee amount in the batch settlement currency."""
        batch = self.get_batch(batch_id)
        total = 0.0
        for fee in batch.fees:
            total += self._convert_to_batch_currency(
                fee.amount, fee.currency, batch.currency
            )
        return round(total, 2)

    # ── Partial settlement ───────────────────────────────────────

    def record_partial_settlement(
        self,
        batch_id: str,
        net_payment_index: int,
        amount: float,
        reference: str = "",
        metadata: Optional[dict] = None,
    ) -> PartialSettlement:
        """Record a partial payment toward a net obligation.

        Allows agents to settle net positions incrementally. The batch must
        be in CALCULATED or DISPUTED state (netting must have been computed).

        Args:
            batch_id: The settlement batch ID
            net_payment_index: Index into batch.net_payments for this obligation
            amount: Amount being paid in this partial settlement
            reference: Optional external reference (e.g. txn hash)
            metadata: Optional metadata

        Returns:
            The created PartialSettlement record
        """
        batch = self.get_batch(batch_id)
        if batch.status not in (SettlementStatus.CALCULATED, SettlementStatus.DISPUTED):
            raise PartialSettlementError(
                f"Cannot record partial settlement in {batch.status.value} state — "
                f"calculate netting first"
            )
        if amount <= 0:
            raise PartialSettlementError("Partial settlement amount must be positive")
        if net_payment_index < 0 or net_payment_index >= len(batch.net_payments):
            raise PartialSettlementError(
                f"Net payment index {net_payment_index} out of range "
                f"(0..{len(batch.net_payments) - 1})"
            )

        net_payment = batch.net_payments[net_payment_index]
        already_paid = self._paid_for_net_payment(batch, net_payment_index)
        outstanding = round(net_payment.amount - already_paid, 2)
        if amount > outstanding + 0.01:  # allow 1 cent tolerance
            raise PartialSettlementError(
                f"Partial settlement {amount} exceeds outstanding balance {outstanding} "
                f"for {net_payment.payer} -> {net_payment.payee}"
            )

        record = PartialSettlement(
            net_payment_index=net_payment_index,
            payer=net_payment.payer,
            payee=net_payment.payee,
            amount=round(amount, 2),
            currency=batch.currency,
            reference=reference,
            metadata=metadata or {},
        )
        batch.partial_settlements.append(record)
        return record

    def get_outstanding_balances(self, batch_id: str) -> list[dict]:
        """Get the outstanding balance for each net payment.

        Returns a list of dicts with payer, payee, total, paid, outstanding.
        """
        batch = self.get_batch(batch_id)
        if not batch.net_payments:
            return []
        results = []
        for idx, np in enumerate(batch.net_payments):
            paid = self._paid_for_net_payment(batch, idx)
            results.append({
                "net_payment_index": idx,
                "payer": np.payer,
                "payee": np.payee,
                "total": np.amount,
                "paid": round(paid, 2),
                "outstanding": round(np.amount - paid, 2),
                "fully_settled": round(np.amount - paid, 2) <= 0.01,
            })
        return results

    def is_fully_settled(self, batch_id: str) -> bool:
        """Check if all net payments have been fully covered by partial settlements."""
        balances = self.get_outstanding_balances(batch_id)
        if not balances:
            return False
        return all(b["fully_settled"] for b in balances)

    def settle_from_partials(self, batch_id: str) -> SettlementBatch:
        """Mark a batch as settled if all net payments are fully covered.

        This is an alternative to full settle() — it validates that partial
        settlements cover all obligations, then finalizes the batch.
        """
        batch = self.get_batch(batch_id)
        if batch.status not in (SettlementStatus.CALCULATED, SettlementStatus.DISPUTED):
            raise PartialSettlementError(
                f"Cannot settle from partials in {batch.status.value} state"
            )
        if not self.is_fully_settled(batch_id):
            outstanding = [
                f"{b['payer']} -> {b['payee']}: {b['outstanding']}"
                for b in self.get_outstanding_balances(batch_id)
                if not b["fully_settled"]
            ]
            raise PartialSettlementError(
                f"Cannot settle — outstanding balances remain: {'; '.join(outstanding)}"
            )

        batch.status = SettlementStatus.SETTLED
        batch.settled_at = datetime.now(timezone.utc)
        if batch.proof:
            batch.proof.settled_at = batch.settled_at
            batch.proof.proof_hash = self._compute_proof_hash(batch, batch.proof)
        return batch

    @staticmethod
    def _paid_for_net_payment(batch: SettlementBatch, idx: int) -> float:
        """Sum all partial settlements for a given net payment index."""
        return sum(
            ps.amount for ps in batch.partial_settlements
            if ps.net_payment_index == idx
        )

    # ── Optimization report ──────────────────────────────────────

    def get_optimization_report(self, batch_id: str) -> dict:
        """Generate a detailed netting optimization report.

        Shows gross vs net volume, savings percentage, participant reduction,
        and per-party breakdown.
        """
        batch = self.get_batch(batch_id)
        if not batch.proof:
            raise InvalidSettlementStateError(
                "No proof — run calculate_netting first"
            )

        proof = batch.proof
        savings_pct = (
            round(proof.netted_savings / proof.total_gross_volume * 100, 2)
            if proof.total_gross_volume > 0 else 0.0
        )
        payment_count = len(batch.net_payments)
        item_count = len([i for i in batch.items if not i.disputed])

        # Payment reduction ratio
        payment_reduction = (
            round((1 - payment_count / item_count) * 100, 2)
            if item_count > 0 else 0.0
        )

        # Per-party breakdown
        party_breakdown = []
        for pos in batch.net_positions:
            party_breakdown.append({
                "party": pos.party,
                "direction": pos.direction.value,
                "gross_out": pos.gross_out,
                "gross_in": pos.gross_in,
                "net": pos.net,
            })

        return {
            "batch_id": batch.id,
            "batch_name": batch.name,
            "currency": batch.currency,
            "items": item_count,
            "participants": proof.participant_count,
            "fees": len(batch.fees),
            "total_fees": self.get_total_fees(batch.id),
            "net_payments": payment_count,
            "gross_volume": proof.total_gross_volume,
            "net_volume": proof.total_net_volume,
            "netted_savings": proof.netted_savings,
            "savings_percentage": savings_pct,
            "payment_reduction_percentage": payment_reduction,
            "partial_settlements": len(batch.partial_settlements),
            "disputed_items": len([i for i in batch.items if i.disputed]),
            "status": batch.status.value,
            "party_breakdown": party_breakdown,
        }

    # ── Internal ─────────────────────────────────────────────────

    @staticmethod
    def _compute_proof_hash(batch: SettlementBatch, proof: SettlementProof) -> str:
        """Compute a SHA-256 proof hash for the settlement."""
        data_parts = [
            proof.settlement_id,
            str(proof.item_count),
            f"{proof.total_gross_volume:.2f}",
            f"{proof.total_net_volume:.2f}",
        ]
        # Include a sorted representation of net payments
        for payment in sorted(batch.net_payments, key=lambda p: (p.payer, p.payee)):
            data_parts.append(f"{payment.payer}->{payment.payee}:{payment.amount:.2f}")
        # Include settled timestamp if present
        if proof.settled_at:
            data_parts.append(proof.settled_at.isoformat())

        data = "|".join(data_parts)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    # ── Serialization ────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize all batches to a dict for persistence."""
        return {
            "batches": [self._batch_to_dict(b) for b in self._batches.values()],
        }

    @staticmethod
    def _batch_to_dict(batch: SettlementBatch) -> dict:
        """Serialize a single batch."""
        return {
            "id": batch.id,
            "name": batch.name,
            "description": batch.description,
            "currency": batch.currency,
            "status": batch.status.value,
            "items": [
                {
                    "id": i.id,
                    "payer": i.payer,
                    "payee": i.payee,
                    "amount": i.amount,
                    "currency": i.currency,
                    "item_type": i.item_type.value,
                    "description": i.description,
                    "reference": i.reference,
                    "metadata": i.metadata,
                    "disputed": i.disputed,
                    "dispute_reason": i.dispute_reason,
                    "created_at": i.created_at.isoformat(),
                }
                for i in batch.items
            ],
            "net_positions": [
                {
                    "party": p.party,
                    "gross_out": p.gross_out,
                    "gross_in": p.gross_in,
                    "net": p.net,
                    "direction": p.direction.value,
                    "currency": p.currency,
                }
                for p in batch.net_positions
            ],
            "net_payments": [
                {
                    "payer": p.payer,
                    "payee": p.payee,
                    "amount": p.amount,
                    "currency": p.currency,
                }
                for p in batch.net_payments
            ],
            "fees": [
                {
                    "id": f.id,
                    "fee_type": f.fee_type.value,
                    "amount": f.amount,
                    "currency": f.currency,
                    "description": f.description,
                    "allocation": f.allocation.value,
                    "item_id": f.item_id,
                    "reference": f.reference,
                    "metadata": f.metadata,
                    "created_at": f.created_at.isoformat(),
                }
                for f in batch.fees
            ],
            "partial_settlements": [
                {
                    "id": ps.id,
                    "net_payment_index": ps.net_payment_index,
                    "payer": ps.payer,
                    "payee": ps.payee,
                    "amount": ps.amount,
                    "currency": ps.currency,
                    "reference": ps.reference,
                    "metadata": ps.metadata,
                    "created_at": ps.created_at.isoformat(),
                }
                for ps in batch.partial_settlements
            ],
            "proof": (
                {
                    "settlement_id": batch.proof.settlement_id,
                    "proof_hash": batch.proof.proof_hash,
                    "item_count": batch.proof.item_count,
                    "total_gross_volume": batch.proof.total_gross_volume,
                    "total_net_volume": batch.proof.total_net_volume,
                    "netted_savings": batch.proof.netted_savings,
                    "participant_count": batch.proof.participant_count,
                    "created_at": batch.proof.created_at.isoformat(),
                    "settled_at": batch.proof.settled_at.isoformat() if batch.proof.settled_at else None,
                }
                if batch.proof
                else None
            ),
            "created_at": batch.created_at.isoformat(),
            "settled_at": batch.settled_at.isoformat() if batch.settled_at else None,
            "metadata": batch.metadata,
        }

    def from_dict(self, data: dict) -> None:
        """Load batches from a serialized dict."""
        self._batches.clear()
        for bd in data.get("batches", []):
            batch = SettlementBatch(
                id=bd["id"],
                name=bd.get("name", ""),
                description=bd.get("description", ""),
                currency=bd.get("currency", "USD"),
                status=SettlementStatus(bd.get("status", "draft")),
                created_at=datetime.fromisoformat(bd["created_at"]) if "created_at" in bd else datetime.now(timezone.utc),
                settled_at=datetime.fromisoformat(bd["settled_at"]) if bd.get("settled_at") else None,
                metadata=bd.get("metadata", {}),
            )
            for item_d in bd.get("items", []):
                batch.items.append(SettlementItem(
                    id=item_d["id"],
                    payer=item_d["payer"],
                    payee=item_d["payee"],
                    amount=item_d["amount"],
                    currency=item_d.get("currency", "USD"),
                    item_type=SettlementItemType(item_d.get("item_type", "custom")),
                    description=item_d.get("description", ""),
                    reference=item_d.get("reference", ""),
                    metadata=item_d.get("metadata", {}),
                    disputed=item_d.get("disputed", False),
                    dispute_reason=item_d.get("dispute_reason", ""),
                    created_at=datetime.fromisoformat(item_d["created_at"]) if "created_at" in item_d else datetime.now(timezone.utc),
                ))
            for pos_d in bd.get("net_positions", []):
                batch.net_positions.append(NetPosition(
                    party=pos_d["party"],
                    gross_out=pos_d["gross_out"],
                    gross_in=pos_d["gross_in"],
                    net=pos_d["net"],
                    direction=NetPositionDirection(pos_d.get("direction", "even")),
                    currency=pos_d.get("currency", "USD"),
                ))
            for pay_d in bd.get("net_payments", []):
                batch.net_payments.append(NetPayment(
                    payer=pay_d["payer"],
                    payee=pay_d["payee"],
                    amount=pay_d["amount"],
                    currency=pay_d.get("currency", "USD"),
                ))
            for fee_d in bd.get("fees", []):
                batch.fees.append(SettlementFee(
                    id=fee_d["id"],
                    fee_type=SettlementFeeType(fee_d.get("fee_type", "processing")),
                    amount=fee_d["amount"],
                    currency=fee_d.get("currency", "USD"),
                    description=fee_d.get("description", ""),
                    allocation=FeeAllocation(fee_d.get("allocation", "payer")),
                    item_id=fee_d.get("item_id"),
                    reference=fee_d.get("reference", ""),
                    metadata=fee_d.get("metadata", {}),
                    created_at=datetime.fromisoformat(fee_d["created_at"]) if "created_at" in fee_d else datetime.now(timezone.utc),
                ))
            for ps_d in bd.get("partial_settlements", []):
                batch.partial_settlements.append(PartialSettlement(
                    id=ps_d["id"],
                    net_payment_index=ps_d["net_payment_index"],
                    payer=ps_d["payer"],
                    payee=ps_d["payee"],
                    amount=ps_d["amount"],
                    currency=ps_d.get("currency", "USD"),
                    reference=ps_d.get("reference", ""),
                    metadata=ps_d.get("metadata", {}),
                    created_at=datetime.fromisoformat(ps_d["created_at"]) if "created_at" in ps_d else datetime.now(timezone.utc),
                ))
            proof_d = bd.get("proof")
            if proof_d:
                batch.proof = SettlementProof(
                    settlement_id=proof_d["settlement_id"],
                    proof_hash=proof_d["proof_hash"],
                    item_count=proof_d["item_count"],
                    total_gross_volume=proof_d["total_gross_volume"],
                    total_net_volume=proof_d["total_net_volume"],
                    netted_savings=proof_d["netted_savings"],
                    participant_count=proof_d["participant_count"],
                    created_at=datetime.fromisoformat(proof_d["created_at"]) if "created_at" in proof_d else datetime.now(timezone.utc),
                    settled_at=datetime.fromisoformat(proof_d["settled_at"]) if proof_d.get("settled_at") else None,
                )
            self._batches[batch.id] = batch
