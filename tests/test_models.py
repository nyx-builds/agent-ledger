"""Tests for agent-ledger core models."""

import pytest
from agent_ledger.models import (
    Account, AccountType, JournalEntry, JournalLine,
    ExchangeRate, AccountBalance, LedgerData,
)


class TestAccountType:
    """Test AccountType enum properties."""

    def test_asset_normal_balance(self):
        assert AccountType.ASSET.normal_balance == "debit"

    def test_liability_normal_balance(self):
        assert AccountType.LIABILITY.normal_balance == "credit"

    def test_equity_normal_balance(self):
        assert AccountType.EQUITY.normal_balance == "credit"

    def test_revenue_normal_balance(self):
        assert AccountType.REVENUE.normal_balance == "credit"

    def test_expense_normal_balance(self):
        assert AccountType.EXPENSE.normal_balance == "debit"

    def test_is_debit_account(self):
        assert AccountType.ASSET.is_debit_account is True
        assert AccountType.EXPENSE.is_debit_account is True
        assert AccountType.LIABILITY.is_debit_account is False
        assert AccountType.EQUITY.is_debit_account is False
        assert AccountType.REVENUE.is_debit_account is False

    def test_is_credit_account(self):
        assert AccountType.LIABILITY.is_credit_account is True
        assert AccountType.EQUITY.is_credit_account is True
        assert AccountType.REVENUE.is_credit_account is True
        assert AccountType.ASSET.is_credit_account is False
        assert AccountType.EXPENSE.is_credit_account is False

    def test_is_permanent(self):
        assert AccountType.ASSET.is_permanent is True
        assert AccountType.LIABILITY.is_permanent is True
        assert AccountType.EQUITY.is_permanent is True
        assert AccountType.REVENUE.is_permanent is False
        assert AccountType.EXPENSE.is_permanent is False

    def test_is_temporary(self):
        assert AccountType.REVENUE.is_temporary is True
        assert AccountType.EXPENSE.is_temporary is True
        assert AccountType.ASSET.is_temporary is False


class TestAccount:
    """Test Account model."""

    def test_create_account(self):
        account = Account(code="cash", name="Cash", account_type=AccountType.ASSET)
        assert account.code == "cash"
        assert account.name == "Cash"
        assert account.account_type == AccountType.ASSET
        assert account.currency == "USD"
        assert account.active is True

    def test_code_normalized_to_lower(self):
        account = Account(code="CASH", name="Cash", account_type=AccountType.ASSET)
        assert account.code == "cash"

    def test_code_stripped(self):
        account = Account(code="  cash  ", name="Cash", account_type=AccountType.ASSET)
        assert account.code == "cash"

    def test_empty_code_raises(self):
        with pytest.raises(ValueError):
            Account(code="", name="Cash", account_type=AccountType.ASSET)

    def test_empty_name_raises(self):
        with pytest.raises(ValueError):
            Account(code="cash", name="", account_type=AccountType.ASSET)

    def test_invalid_currency_length(self):
        with pytest.raises(ValueError):
            Account(code="cash", name="Cash", account_type=AccountType.ASSET, currency="US")

    def test_currency_uppercased(self):
        account = Account(code="cash", name="Cash", account_type=AccountType.ASSET, currency="usd")
        assert account.currency == "USD"


class TestJournalLine:
    """Test JournalLine model."""

    def test_debit_line(self):
        line = JournalLine(account_code="cash", debit=100.0)
        assert line.debit == 100.0
        assert line.credit == 0.0
        assert line.is_debit is True
        assert line.is_credit is False
        assert line.side == "debit"
        assert line.amount == 100.0

    def test_credit_line(self):
        line = JournalLine(account_code="revenue", credit=100.0)
        assert line.debit == 0.0
        assert line.credit == 100.0
        assert line.is_credit is True
        assert line.is_debit is False
        assert line.side == "credit"

    def test_negative_debit_raises(self):
        with pytest.raises(ValueError):
            JournalLine(account_code="cash", debit=-10.0)

    def test_negative_credit_raises(self):
        with pytest.raises(ValueError):
            JournalLine(account_code="revenue", credit=-10.0)

    def test_amounts_rounded(self):
        line = JournalLine(account_code="cash", debit=100.123)
        assert line.debit == 100.12

    def test_empty_account_code_raises(self):
        with pytest.raises(ValueError):
            JournalLine(account_code="", debit=100.0)

    def test_account_code_normalized(self):
        line = JournalLine(account_code="CASH", debit=100.0)
        assert line.account_code == "cash"


class TestJournalEntry:
    """Test JournalEntry model."""

    def test_balanced_entry(self):
        entry = JournalEntry(
            description="Sale",
            lines=[
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        assert entry.total_debits == 100.0
        assert entry.total_credits == 100.0

    def test_unbalanced_entry_raises(self):
        with pytest.raises(ValueError, match="does not balance"):
            JournalEntry(
                description="Bad entry",
                lines=[
                    JournalLine(account_code="cash", debit=100.0),
                    JournalLine(account_code="revenue", credit=50.0),
                ],
            )

    def test_zero_line_raises(self):
        with pytest.raises(ValueError, match="non-zero"):
            JournalEntry(
                description="Bad entry",
                lines=[
                    JournalLine(account_code="cash", debit=0.0, credit=0.0),
                    JournalLine(account_code="revenue", credit=100.0),
                ],
            )

    def test_both_sides_raises(self):
        with pytest.raises(ValueError, match="both debit and credit"):
            JournalEntry(
                description="Bad entry",
                lines=[
                    JournalLine(account_code="cash", debit=100.0, credit=100.0),
                    JournalLine(account_code="revenue", credit=100.0),
                ],
            )

    def test_minimum_two_lines(self):
        with pytest.raises(ValueError):
            JournalEntry(
                description="Bad entry",
                lines=[JournalLine(account_code="cash", debit=100.0)],
            )

    def test_multi_line_entry(self):
        entry = JournalEntry(
            description="Complex entry",
            lines=[
                JournalLine(account_code="cash", debit=1000.0),
                JournalLine(account_code="inventory", debit=200.0),
                JournalLine(account_code="revenue", credit=1200.0),
            ],
        )
        assert entry.total_debits == 1200.0
        assert entry.total_credits == 1200.0

    def test_entry_has_id(self):
        entry = JournalEntry(
            description="Test",
            lines=[
                JournalLine(account_code="cash", debit=100.0),
                JournalLine(account_code="revenue", credit=100.0),
            ],
        )
        assert entry.id is not None
        assert len(entry.id) > 0


class TestExchangeRate:
    """Test ExchangeRate model."""

    def test_create_rate(self):
        rate = ExchangeRate(from_currency="USD", to_currency="EUR", rate=0.85)
        assert rate.from_currency == "USD"
        assert rate.to_currency == "EUR"
        assert rate.rate == 0.85

    def test_zero_rate_raises(self):
        with pytest.raises(ValueError):
            ExchangeRate(from_currency="USD", to_currency="EUR", rate=0.0)

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError):
            ExchangeRate(from_currency="USD", to_currency="EUR", rate=-1.0)


class TestAccountBalance:
    """Test AccountBalance model."""

    def test_debit_account_balance(self):
        balance = AccountBalance(
            account_code="cash",
            account_name="Cash",
            account_type=AccountType.ASSET,
            currency="USD",
            debit_total=1000.0,
            credit_total=200.0,
        )
        assert balance.raw_balance == 800.0
        assert balance.balance == 800.0  # Asset: debit - credit

    def test_credit_account_balance(self):
        balance = AccountBalance(
            account_code="revenue",
            account_name="Revenue",
            account_type=AccountType.REVENUE,
            currency="USD",
            debit_total=100.0,
            credit_total=500.0,
        )
        assert balance.raw_balance == -400.0
        assert balance.balance == 400.0  # Revenue: credit - debit

    def test_zero_balance(self):
        balance = AccountBalance(
            account_code="cash",
            account_name="Cash",
            account_type=AccountType.ASSET,
            currency="USD",
        )
        assert balance.raw_balance == 0.0
        assert balance.balance == 0.0


class TestLedgerData:
    """Test LedgerData model."""

    def test_create_empty_ledger(self):
        data = LedgerData()
        assert data.name == "Default Ledger"
        assert data.base_currency == "USD"
        assert len(data.accounts) == 0
        assert len(data.entries) == 0

    def test_ledger_with_accounts(self):
        account = Account(code="cash", name="Cash", account_type=AccountType.ASSET)
        data = LedgerData(accounts={"cash": account})
        assert "cash" in data.accounts
