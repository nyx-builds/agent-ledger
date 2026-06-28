"""Account hierarchy and rollup for agent-ledger."""

from __future__ import annotations

from typing import Optional

from .models import Account, AccountType, AccountBalance
from .ledger import Ledger
from .exceptions import AccountNotFoundError, AccountHasChildrenError


class AccountHierarchy:
    """Manages parent-child account relationships and balance rollup."""

    def __init__(self, ledger: Ledger):
        self._ledger = ledger

    def get_children(self, parent_code: str) -> list[Account]:
        """Get direct children of an account."""
        parent_code = parent_code.strip().lower()
        # Verify parent exists
        self._ledger.get_account(parent_code)

        children = []
        for account in self._ledger.list_accounts():
            if account.parent_code == parent_code:
                children.append(account)
        return sorted(children, key=lambda a: a.code)

    def get_all_descendants(self, parent_code: str) -> list[Account]:
        """Get all descendants (children, grandchildren, etc.) of an account."""
        parent_code = parent_code.strip().lower()
        descendants = []
        queue = [parent_code]

        while queue:
            current = queue.pop(0)
            children = self.get_children(current)
            for child in children:
                descendants.append(child)
                queue.append(child.code)

        return descendants

    def get_parent(self, account_code: str) -> Optional[Account]:
        """Get the parent account, if any."""
        account = self._ledger.get_account(account_code)
        if account.parent_code:
            return self._ledger.get_account(account.parent_code)
        return None

    def get_ancestors(self, account_code: str) -> list[Account]:
        """Get all ancestors (parent, grandparent, etc.) of an account."""
        ancestors = []
        account = self._ledger.get_account(account_code)

        while account.parent_code:
            try:
                parent = self._ledger.get_account(account.parent_code)
                ancestors.append(parent)
                account = parent
            except AccountNotFoundError:
                break

        return ancestors

    def get_root(self, account_code: str) -> Account:
        """Get the root ancestor of an account."""
        ancestors = self.get_ancestors(account_code)
        if ancestors:
            return ancestors[-1]
        return self._ledger.get_account(account_code)

    def get_rollup_balance(self, account_code: str) -> AccountBalance:
        """Get the rolled-up balance including all descendants.

        The account's own balance plus all descendants' balances.
        """
        account = self._ledger.get_account(account_code)
        own_balance = self._ledger.get_account_balance(account_code)

        # Collect all descendants
        descendants = self.get_all_descendants(account_code)

        total_debit = own_balance.debit_total
        total_credit = own_balance.credit_total

        for desc in descendants:
            desc_balance = self._ledger.get_account_balance(desc.code)
            total_debit += desc_balance.debit_total
            total_credit += desc_balance.credit_total

        return AccountBalance(
            account_code=account_code,
            account_name=account.name,
            account_type=account.account_type,
            currency=account.currency,
            debit_total=round(total_debit, 2),
            credit_total=round(total_credit, 2),
        )

    def is_leaf(self, account_code: str) -> bool:
        """Check if an account has no children (is a leaf node)."""
        return len(self.get_children(account_code)) == 0

    def is_root(self, account_code: str) -> bool:
        """Check if an account has no parent."""
        account = self._ledger.get_account(account_code)
        return account.parent_code is None

    def get_depth(self, account_code: str) -> int:
        """Get the depth of an account in the hierarchy (root = 0)."""
        return len(self.get_ancestors(account_code))

    def get_tree(self, root_code: Optional[str] = None) -> list[dict]:
        """Get a tree structure of accounts.

        If root_code is provided, returns tree rooted at that account.
        Otherwise returns all root accounts and their descendants.

        Returns a list of dicts with 'account', 'balance', 'rollup_balance', 'children'.
        """
        if root_code:
            roots = [self._ledger.get_account(root_code)]
        else:
            roots = [
                a for a in self._ledger.list_accounts()
                if a.parent_code is None
            ]

        result = []
        for root in roots:
            result.append(self._build_tree_node(root))
        return result

    def _build_tree_node(self, account: Account) -> dict:
        """Recursively build a tree node."""
        children = self.get_children(account.code)
        balance = self._ledger.get_account_balance(account.code)
        rollup = self.get_rollup_balance(account.code)

        return {
            "account": account,
            "balance": balance,
            "rollup_balance": rollup,
            "depth": self.get_depth(account.code),
            "children": [self._build_tree_node(c) for c in children],
        }

    def format_tree(self, root_code: Optional[str] = None) -> str:
        """Format the account hierarchy as a text tree."""
        trees = self.get_tree(root_code)
        lines = []
        for tree in trees:
            self._format_tree_node(tree, lines, is_last=True)
        return "\n".join(lines)

    def _format_tree_node(self, node: dict, lines: list, prefix: str = "", is_last: bool = True) -> None:
        """Recursively format a tree node."""
        account = node["account"]
        balance = node["balance"].balance
        rollup = node["rollup_balance"].balance

        connector = "└── " if is_last else "├── "
        has_children = bool(node["children"])

        if prefix == "" and is_last:
            # Root node
            if has_children:
                rollup_str = f" (rollup: {rollup:,.2f})" if abs(rollup - balance) > 0.005 else ""
            else:
                rollup_str = ""
            lines.append(f"{account.code}  {account.name}  [{account.account_type.value}]  Balance: {balance:,.2f}{rollup_str}")
            child_prefix = ""
        else:
            if has_children:
                rollup_str = f" (rollup: {rollup:,.2f})" if abs(rollup - balance) > 0.005 else ""
            else:
                rollup_str = ""
            lines.append(f"{prefix}{connector}{account.code}  {account.name}  [{account.account_type.value}]  {balance:,.2f}{rollup_str}")
            child_prefix = prefix + ("    " if is_last else "│   ")

        for i, child in enumerate(node["children"]):
            is_last_child = (i == len(node["children"]) - 1)
            self._format_tree_node(child, lines, child_prefix, is_last_child)

    def validate_hierarchy(self) -> list[str]:
        """Validate the account hierarchy for issues.

        Returns a list of warning messages (empty if no issues).
        """
        warnings = []

        for account in self._ledger.list_accounts():
            # Check that parent exists
            if account.parent_code:
                try:
                    parent = self._ledger.get_account(account.parent_code)
                    # Check type consistency
                    if parent.account_type != account.account_type:
                        warnings.append(
                            f"Account '{account.code}' ({account.account_type.value}) "
                            f"has parent '{parent.code}' ({parent.account_type.value}) "
                            f"with different type"
                        )
                except AccountNotFoundError:
                    warnings.append(
                        f"Account '{account.code}' references non-existent "
                        f"parent '{account.parent_code}'"
                    )

            # Check for circular references
            visited = {account.code}
            current = account
            while current.parent_code:
                if current.parent_code in visited:
                    warnings.append(
                        f"Circular reference detected involving account '{account.code}'"
                    )
                    break
                visited.add(current.parent_code)
                try:
                    current = self._ledger.get_account(current.parent_code)
                except AccountNotFoundError:
                    break

        return warnings
