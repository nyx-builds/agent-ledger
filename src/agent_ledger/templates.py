"""Chart of accounts templates for agent-ledger."""

from __future__ import annotations

from .models import AccountType, Account


# Standard account code ranges:
# 1000-1999: Assets
# 2000-2999: Liabilities
# 3000-3999: Equity
# 4000-4999: Revenue
# 5000-5999: Expenses

SOLO_BUSINESS = [
    ("1000", "Cash", AccountType.ASSET, "USD", "Cash on hand"),
    ("1100", "Bank Account", AccountType.ASSET, "USD", "Primary bank account"),
    ("1200", "Accounts Receivable", AccountType.ASSET, "USD", "Money owed to you"),
    ("1300", "Inventory", AccountType.ASSET, "USD", "Goods for sale"),
    ("1400", "Equipment", AccountType.ASSET, "USD", "Business equipment"),
    ("1500", "Prepaid Expenses", AccountType.ASSET, "USD", "Expenses paid in advance"),
    ("2000", "Accounts Payable", AccountType.LIABILITY, "USD", "Money you owe"),
    ("2100", "Credit Card", AccountType.LIABILITY, "USD", "Credit card balance"),
    ("2200", "Loans Payable", AccountType.LIABILITY, "USD", "Outstanding loans"),
    ("2300", "Accrued Expenses", AccountType.LIABILITY, "USD", "Expenses incurred but not paid"),
    ("3000", "Owner's Equity", AccountType.EQUITY, "USD", "Owner investment"),
    ("3100", "Retained Earnings", AccountType.EQUITY, "USD", "Accumulated earnings"),
    ("4000", "Sales Revenue", AccountType.REVENUE, "USD", "Revenue from sales"),
    ("4100", "Service Revenue", AccountType.REVENUE, "USD", "Revenue from services"),
    ("4200", "Other Income", AccountType.REVENUE, "USD", "Interest, dividends, etc."),
    ("5000", "Cost of Goods Sold", AccountType.EXPENSE, "USD", "Direct cost of products"),
    ("5100", "Rent Expense", AccountType.EXPENSE, "USD", "Office/workspace rent"),
    ("5200", "Utilities Expense", AccountType.EXPENSE, "USD", "Electric, water, internet"),
    ("5300", "Salaries Expense", AccountType.EXPENSE, "USD", "Employee compensation"),
    ("5400", "Marketing Expense", AccountType.EXPENSE, "USD", "Advertising and marketing"),
    ("5500", "Office Supplies", AccountType.EXPENSE, "USD", "Office supplies and software"),
    ("5600", "Insurance Expense", AccountType.EXPENSE, "USD", "Business insurance"),
    ("5700", "Travel Expense", AccountType.EXPENSE, "USD", "Business travel"),
    ("5800", "Professional Services", AccountType.EXPENSE, "USD", "Legal, accounting, consulting"),
    ("5900", "Depreciation Expense", AccountType.EXPENSE, "USD", "Asset depreciation"),
]

STARTUP_TEMPLATE = [
    ("1000", "Cash", AccountType.ASSET, "USD", "Cash on hand"),
    ("1010", "Checking Account", AccountType.ASSET, "USD", "Primary checking"),
    ("1020", "Savings Account", AccountType.ASSET, "USD", "Reserve savings"),
    ("1100", "Accounts Receivable", AccountType.ASSET, "USD", "Outstanding invoices"),
    ("1200", "Inventory", AccountType.ASSET, "USD", "Product inventory"),
    ("1300", "Prepaid Expenses", AccountType.ASSET, "USD", "Prepaid items"),
    ("1400", "Furniture & Equipment", AccountType.ASSET, "USD", "Office furniture and equipment"),
    ("1500", "Computers & Software", AccountType.ASSET, "USD", "Technology assets"),
    ("1600", "Accumulated Depreciation", AccountType.ASSET, "USD", "Contra-asset for depreciation"),
    ("2000", "Accounts Payable", AccountType.LIABILITY, "USD", "Vendor bills"),
    ("2100", "Credit Cards Payable", AccountType.LIABILITY, "USD", "Credit card balances"),
    ("2200", "Payroll Liabilities", AccountType.LIABILITY, "USD", "Withholdings and benefits"),
    ("2300", "Sales Tax Payable", AccountType.LIABILITY, "USD", "Collected sales tax"),
    ("2400", "Notes Payable", AccountType.LIABILITY, "USD", "Loan balances"),
    ("2500", "Deferred Revenue", AccountType.LIABILITY, "USD", "Prepaid by customers"),
    ("3000", "Common Stock", AccountType.EQUITY, "USD", "Issued shares"),
    ("3100", "Additional Paid-in Capital", AccountType.EQUITY, "USD", "Capital above par value"),
    ("3200", "Retained Earnings", AccountType.EQUITY, "USD", "Accumulated earnings"),
    ("4000", "Product Revenue", AccountType.REVENUE, "USD", "Revenue from products"),
    ("4100", "Service Revenue", AccountType.REVENUE, "USD", "Revenue from services"),
    ("4200", "Subscription Revenue", AccountType.REVENUE, "USD", "Recurring subscription income"),
    ("4300", "Interest Income", AccountType.REVENUE, "USD", "Bank interest earned"),
    ("5000", "Cost of Goods Sold", AccountType.EXPENSE, "USD", "Direct product costs"),
    ("5100", "Salaries & Wages", AccountType.EXPENSE, "USD", "Employee compensation"),
    ("5200", "Rent Expense", AccountType.EXPENSE, "USD", "Office rent"),
    ("5300", "Marketing & Advertising", AccountType.EXPENSE, "USD", "Customer acquisition"),
    ("5400", "Cloud & Hosting", AccountType.EXPENSE, "USD", "AWS, GCP, etc."),
    ("5500", "Software Subscriptions", AccountType.EXPENSE, "USD", "SaaS tools"),
    ("5600", "Professional Services", AccountType.EXPENSE, "USD", "Legal, accounting"),
    ("5700", "Travel & Entertainment", AccountType.EXPENSE, "USD", "Business travel"),
    ("5800", "Office Supplies", AccountType.EXPENSE, "USD", "Day-to-day supplies"),
    ("5900", "Insurance", AccountType.EXPENSE, "USD", "Business insurance"),
    ("5950", "Depreciation Expense", AccountType.EXPENSE, "USD", "Asset depreciation"),
    ("5980", "Interest Expense", AccountType.EXPENSE, "USD", "Loan interest"),
    ("5990", "Tax Expense", AccountType.EXPENSE, "USD", "Income taxes"),
]

FREELANCER_TEMPLATE = [
    ("1000", "Cash", AccountType.ASSET, "USD", "Cash on hand"),
    ("1100", "Business Bank", AccountType.ASSET, "USD", "Business bank account"),
    ("1200", "Accounts Receivable", AccountType.ASSET, "USD", "Unpaid invoices"),
    ("1300", "Equipment", AccountType.ASSET, "USD", "Computer, camera, etc."),
    ("2000", "Accounts Payable", AccountType.LIABILITY, "USD", "Outstanding bills"),
    ("2100", "Credit Card", AccountType.LIABILITY, "USD", "Business credit card"),
    ("2200", "Taxes Payable", AccountType.LIABILITY, "USD", "Estimated taxes owed"),
    ("3000", "Owner's Equity", AccountType.EQUITY, "USD", "Owner investment"),
    ("3100", "Retained Earnings", AccountType.EQUITY, "USD", "Accumulated earnings"),
    ("4000", "Service Revenue", AccountType.REVENUE, "USD", "Freelance income"),
    ("4100", "Consulting Revenue", AccountType.REVENUE, "USD", "Consulting fees"),
    ("4200", "Other Income", AccountType.REVENUE, "USD", "Misc income"),
    ("5000", "Software & Tools", AccountType.EXPENSE, "USD", "SaaS subscriptions"),
    ("5100", "Office Expense", AccountType.EXPENSE, "USD", "Home office costs"),
    ("5200", "Internet & Phone", AccountType.EXPENSE, "USD", "Connectivity"),
    ("5300", "Travel Expense", AccountType.EXPENSE, "USD", "Business travel"),
    ("5400", "Marketing", AccountType.EXPENSE, "USD", "Self-promotion"),
    ("5500", "Professional Services", AccountType.EXPENSE, "USD", "Accounting, legal"),
    ("5600", "Insurance", AccountType.EXPENSE, "USD", "Business insurance"),
    ("5700", "Equipment Depreciation", AccountType.EXPENSE, "USD", "Asset depreciation"),
]

TEMPLATES = {
    "solo": ("Solo Business", SOLO_BUSINESS),
    "startup": ("Startup", STARTUP_TEMPLATE),
    "freelancer": ("Freelancer", FREELANCER_TEMPLATE),
}


def get_template_names() -> list[dict]:
    """Get available template names and descriptions."""
    return [
        {"key": key, "name": name, "account_count": len(accounts)}
        for key, (name, accounts) in TEMPLATES.items()
    ]


def apply_template(ledger, template_key: str) -> list[Account]:
    """Apply a chart of accounts template to a ledger.

    Args:
        ledger: The ledger to create accounts in
        template_key: Key of the template to apply

    Returns:
        List of created Account objects

    Raises:
        ValueError: If template key is not found
    """
    if template_key not in TEMPLATES:
        available = ", ".join(TEMPLATES.keys())
        raise ValueError(
            f"Unknown template '{template_key}'. Available: {available}"
        )

    name, accounts = TEMPLATES[template_key]
    created = []

    for code, acct_name, acct_type, currency, description in accounts:
        try:
            account = ledger.create_account(
                code=code,
                name=acct_name,
                account_type=acct_type,
                currency=currency,
                description=description,
            )
            created.append(account)
        except Exception:
            # Skip if account already exists
            pass

    return created
