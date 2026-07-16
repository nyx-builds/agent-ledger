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
from typing import Optional

from .exceptions import (
    LedgerError,
    SettlementNotFoundError,
    SettlementItemNotFoundError,
    InvalidSettlementStateError,
    DuplicateSettlementItemError,
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
    net_positions: list[NetPosition] = field(default_factory=list)
    net_payments: list[NetPayment] = field(default_factory=list)
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

    def __init__(self):
        self._batches: dict[str, SettlementBatch] = {}

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
        # bilateral[payer][payee] = net amount payer owes payee
        bilateral: dict[str, dict[str, float]] = {}

        for item in active_items:
            payer = item.payer
            payee = item.payee
            if payer not in bilateral:
                bilateral[payer] = {}
            if payee not in bilateral:
                bilateral[payee] = {}

            # If payee also owes payer, net them
            reverse = bilateral.get(payee, {}).get(payer, 0.0)
            if reverse > 0:
                # Net: reduce both
                netted = min(reverse, item.amount)
                bilateral[payee][payer] = round(reverse - netted, 2)
                remaining = round(item.amount - netted, 2)
                if remaining > 0:
                    bilateral[payer][payee] = bilateral.get(payer, {}).get(payee, 0.0) + remaining
            else:
                bilateral[payer][payee] = bilateral.get(payer, {}).get(payee, 0.0) + item.amount

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
            gross_out[item.payer] = gross_out.get(item.payer, 0.0) + item.amount
            gross_in[item.payee] = gross_in.get(item.payee, 0.0) + item.amount

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
