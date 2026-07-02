"""CSV import for agent-ledger — import accounts and entries from CSV."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from .models import AccountType, JournalLine
from .ledger import Ledger
from .exceptions import LedgerError


class CSVImportResult:
    """Result of a CSV import operation."""

    def __init__(self):
        self.imported: int = 0
        self.skipped: int = 0
        self.errors: list[str] = []

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def __repr__(self) -> str:
        return (
            f"CSVImportResult(imported={self.imported}, "
            f"skipped={self.skipped}, errors={len(self.errors)})"
        )


def import_accounts_csv(
    ledger: Ledger,
    csv_content: str,
    skip_errors: bool = False,
) -> CSVImportResult:
    """Import accounts from CSV content.

    Expected columns: code, name, type, currency, description, parent_code
    - code and name are required
    - type defaults to 'asset' if not specified
    - currency defaults to ledger base_currency if not specified
    - description and parent_code are optional

    Args:
        ledger: The ledger to import into
        csv_content: CSV string with account data
        skip_errors: If True, skip rows with errors instead of failing

    Returns:
        CSVImportResult with counts and any errors
    """
    result = CSVImportResult()
    reader = csv.DictReader(io.StringIO(csv_content))

    for row_num, row in enumerate(reader, start=2):  # header is row 1
        try:
            code = row.get("code", "").strip()
            name = row.get("name", "").strip()

            if not code or not name:
                result.errors.append(f"Row {row_num}: code and name are required")
                if not skip_errors:
                    return result
                continue

            # Parse account type
            type_str = row.get("type", "asset").strip().lower()
            try:
                account_type = AccountType(type_str)
            except ValueError:
                result.errors.append(
                    f"Row {row_num}: invalid account type '{type_str}'"
                )
                if not skip_errors:
                    return result
                continue

            currency = row.get("currency", "").strip() or ledger.data.base_currency
            description = row.get("description", "").strip()
            parent_code = row.get("parent_code", "").strip() or None

            ledger.create_account(
                code=code,
                name=name,
                account_type=account_type,
                currency=currency,
                description=description,
                parent_code=parent_code,
            )
            result.imported += 1

        except LedgerError as e:
            result.skipped += 1
            result.errors.append(f"Row {row_num}: {e}")
            if not skip_errors:
                return result
        except Exception as e:
            result.errors.append(f"Row {row_num}: unexpected error: {e}")
            if not skip_errors:
                return result

    return result


def import_entries_csv(
    ledger: Ledger,
    csv_content: str,
    skip_errors: bool = False,
) -> CSVImportResult:
    """Import journal entries from CSV content.

    Expected columns: entry_description, account_code, debit, credit
    - Lines with the same entry_description are grouped into one entry
    - debit and credit default to 0 if not specified
    - Lines must balance within each entry group

    Args:
        ledger: The ledger to import into
        csv_content: CSV string with entry data
        skip_errors: If True, skip rows with errors instead of failing

    Returns:
        CSVImportResult with counts and any errors
    """
    result = CSVImportResult()
    reader = csv.DictReader(io.StringIO(csv_content))

    # Group lines by entry description
    entry_groups: dict[str, list[dict]] = {}
    entry_order: list[str] = []

    for row_num, row in enumerate(reader, start=2):
        entry_desc = row.get("entry_description", "").strip()
        account_code = row.get("account_code", "").strip()

        if not entry_desc or not account_code:
            result.errors.append(
                f"Row {row_num}: entry_description and account_code are required"
            )
            if not skip_errors:
                return result
            continue

        try:
            debit = float(row.get("debit", "0").strip() or "0")
            credit = float(row.get("credit", "0").strip() or "0")
        except ValueError:
            result.errors.append(f"Row {row_num}: invalid debit/credit amount")
            if not skip_errors:
                return result
            continue

        if entry_desc not in entry_groups:
            entry_groups[entry_desc] = []
            entry_order.append(entry_desc)

        entry_groups[entry_desc].append({
            "row": row_num,
            "account_code": account_code,
            "debit": debit,
            "credit": credit,
            "line_description": row.get("line_description", "").strip(),
        })

    # Post each entry group
    for entry_desc in entry_order:
        lines_data = entry_groups[entry_desc]
        journal_lines = []

        for line_data in lines_data:
            try:
                journal_lines.append(JournalLine(
                    account_code=line_data["account_code"],
                    debit=round(line_data["debit"], 2),
                    credit=round(line_data["credit"], 2),
                    description=line_data.get("line_description", ""),
                ))
            except Exception as e:
                result.errors.append(
                    f"Row {line_data['row']}: invalid line: {e}"
                )
                if not skip_errors:
                    return result
                continue

        if len(journal_lines) < 2:
            result.errors.append(
                f"Entry '{entry_desc}': needs at least 2 valid lines"
            )
            if not skip_errors:
                return result
            continue

        try:
            ledger.post_entry(
                description=entry_desc,
                lines=journal_lines,
            )
            result.imported += 1
        except (LedgerError, Exception) as e:
            result.skipped += 1
            result.errors.append(f"Entry '{entry_desc}': {e}")
            if not skip_errors:
                return result

    return result
