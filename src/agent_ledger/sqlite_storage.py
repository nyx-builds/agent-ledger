"""SQLite-backed persistence layer for agent-ledger.

Provides a performant, concurrent-access-safe storage backend using SQLite.
Auto-detected when the filepath ends with .db; otherwise falls back to JSON storage.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import (
    Account, AccountType, AccountBalance,
    JournalEntry, JournalLine, ExchangeRate,
    LedgerData, AuditLogData,
)


class SQLiteStorage:
    """SQLite-backed storage for agent-ledger.

    Stores accounts, entries, exchange rates, audit log, and metadata
    in a normalized SQLite database for fast queries and concurrent access.
    """

    def __init__(self, filepath: Optional[Path] = None):
        self.filepath = filepath or Path("ledger.db")
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.filepath),
                check_same_thread=False,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
            self._create_tables()
        return self._conn

    def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ledger_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL DEFAULT 'Default Ledger',
                base_currency TEXT NOT NULL DEFAULT 'USD',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS accounts (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                account_type TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                description TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                parent_code TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS journal_entries (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                reconciled INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS journal_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                account_code TEXT NOT NULL,
                debit REAL NOT NULL DEFAULT 0.0,
                credit REAL NOT NULL DEFAULT 0.0,
                line_description TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (entry_id) REFERENCES journal_entries(id) ON DELETE CASCADE,
                FOREIGN KEY (account_code) REFERENCES accounts(code)
            );

            CREATE INDEX IF NOT EXISTS idx_lines_entry ON journal_lines(entry_id);
            CREATE INDEX IF NOT EXISTS idx_lines_account ON journal_lines(account_code);

            CREATE TABLE IF NOT EXISTS exchange_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_currency TEXT NOT NULL,
                to_currency TEXT NOT NULL,
                rate REAL NOT NULL,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual'
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'system',
                timestamp TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                before_state TEXT,
                after_state TEXT
            );

            CREATE TABLE IF NOT EXISTS closed_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                closing_entry_id TEXT NOT NULL,
                closed_at TEXT NOT NULL,
                net_income REAL NOT NULL DEFAULT 0.0,
                revenue_accounts_closed TEXT NOT NULL DEFAULT '[]',
                expense_accounts_closed TEXT NOT NULL DEFAULT '[]',
                retained_earnings_account TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS bank_statements (
                id TEXT PRIMARY KEY,
                account_code TEXT NOT NULL,
                statement_date TEXT,
                opening_balance REAL NOT NULL DEFAULT 0.0,
                closing_balance REAL NOT NULL DEFAULT 0.0,
                currency TEXT NOT NULL DEFAULT 'USD',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bank_statement_lines (
                id TEXT PRIMARY KEY,
                statement_id TEXT NOT NULL,
                date TEXT,
                description TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL DEFAULT 0.0,
                reference TEXT NOT NULL DEFAULT '',
                matched_entry_id TEXT,
                status TEXT NOT NULL DEFAULT 'unmatched',
                FOREIGN KEY (statement_id) REFERENCES bank_statements(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_statement_lines_stmt ON bank_statement_lines(statement_id);

            CREATE TABLE IF NOT EXISTS budgets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                period_start TEXT,
                period_end TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                total_budgeted REAL NOT NULL DEFAULT 0.0,
                total_actual REAL NOT NULL DEFAULT 0.0,
                total_variance REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS budget_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                budget_id TEXT NOT NULL,
                account_code TEXT NOT NULL,
                budgeted_amount REAL NOT NULL DEFAULT 0.0,
                actual_amount REAL NOT NULL DEFAULT 0.0,
                variance REAL NOT NULL DEFAULT 0.0,
                variance_pct REAL NOT NULL DEFAULT 0.0,
                FOREIGN KEY (budget_id) REFERENCES budgets(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_budget_lines_budget ON budget_lines(budget_id);

            CREATE TABLE IF NOT EXISTS fiscal_years (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fiscal_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fiscal_year_id TEXT NOT NULL,
                name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                period_type TEXT NOT NULL DEFAULT 'month',
                FOREIGN KEY (fiscal_year_id) REFERENCES fiscal_years(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_fiscal_periods_fy ON fiscal_periods(fiscal_year_id);

            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    def exists(self) -> bool:
        """Check if the database file exists."""
        return self.filepath.exists()

    def init(self, name: str = "Default Ledger", base_currency: str = "USD") -> LedgerData:
        """Initialize a new ledger database."""
        if self.exists():
            # Check if already initialized
            conn = self._get_conn()
            row = conn.execute("SELECT 1 FROM ledger_meta").fetchone()
            if row:
                raise FileExistsError(f"Ledger already initialized at {self.filepath}")

        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO ledger_meta (id, name, base_currency, created_at, updated_at) VALUES (1, ?, ?, ?, ?)",
            (name, base_currency, now, now),
        )
        conn.commit()
        return LedgerData(name=name, base_currency=base_currency)

    def load(self) -> LedgerData:
        """Load all ledger data from SQLite into a LedgerData model."""
        conn = self._get_conn()

        # Load metadata
        meta = conn.execute("SELECT * FROM ledger_meta WHERE id = 1").fetchone()
        if meta is None:
            from .exceptions import LedgerNotInitializedError
            raise LedgerNotInitializedError(
                f"Ledger not initialized at {self.filepath}. Run 'agent-ledger init' first."
            )

        # Load accounts
        account_rows = conn.execute("SELECT * FROM accounts ORDER BY code").fetchall()
        accounts = {}
        for r in account_rows:
            accounts[r["code"]] = Account(
                code=r["code"],
                name=r["name"],
                account_type=AccountType(r["account_type"]),
                currency=r["currency"],
                description=r["description"],
                active=bool(r["active"]),
                parent_code=r["parent_code"],
                metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                tags=json.loads(r["tags"]) if r["tags"] else [],
                created_at=datetime.fromisoformat(r["created_at"]),
            )

        # Load entries with lines
        entry_rows = conn.execute("SELECT * FROM journal_entries ORDER BY timestamp").fetchall()
        entries = []
        for er in entry_rows:
            line_rows = conn.execute(
                "SELECT * FROM journal_lines WHERE entry_id = ? ORDER BY id",
                (er["id"],),
            ).fetchall()
            lines = []
            for lr in line_rows:
                lines.append(JournalLine(
                    account_code=lr["account_code"],
                    debit=lr["debit"],
                    credit=lr["credit"],
                    description=lr["line_description"],
                ))

            entries.append(JournalEntry(
                id=er["id"],
                description=er["description"],
                lines=lines,
                timestamp=datetime.fromisoformat(er["timestamp"]),
                tags=json.loads(er["tags"]) if er["tags"] else [],
                reconciled=bool(er["reconciled"]),
                metadata=json.loads(er["metadata"]) if er["metadata"] else {},
            ))

        # Load exchange rates
        rate_rows = conn.execute("SELECT * FROM exchange_rates ORDER BY id").fetchall()
        exchange_rates = []
        for r in rate_rows:
            exchange_rates.append(ExchangeRate(
                from_currency=r["from_currency"],
                to_currency=r["to_currency"],
                rate=r["rate"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                source=r["source"],
            ))

        # Load audit log
        audit_rows = conn.execute("SELECT * FROM audit_log ORDER BY timestamp").fetchall()
        audit_entries = []
        for a in audit_rows:
            audit_entries.append({
                "id": a["id"],
                "action": a["action"],
                "actor": a["actor"],
                "timestamp": a["timestamp"],
                "details": json.loads(a["details"]) if a["details"] else {},
                "before": json.loads(a["before_state"]) if a["before_state"] else None,
                "after": json.loads(a["after_state"]) if a["after_state"] else None,
            })

        # Load closed periods
        cp_rows = conn.execute("SELECT * FROM closed_periods ORDER BY id").fetchall()
        closed_periods = []
        for c in cp_rows:
            closed_periods.append({
                "closing_entry_id": c["closing_entry_id"],
                "closed_at": c["closed_at"],
                "net_income": c["net_income"],
                "revenue_accounts_closed": json.loads(c["revenue_accounts_closed"]),
                "expense_accounts_closed": json.loads(c["expense_accounts_closed"]),
                "retained_earnings_account": c["retained_earnings_account"],
            })

        # Load bank statements with lines
        stmt_rows = conn.execute("SELECT * FROM bank_statements ORDER BY created_at DESC").fetchall()
        bank_statements = []
        for s in stmt_rows:
            sl_rows = conn.execute(
                "SELECT * FROM bank_statement_lines WHERE statement_id = ? ORDER BY id",
                (s["id"],),
            ).fetchall()
            stmt_lines = []
            for sl in sl_rows:
                stmt_lines.append({
                    "id": sl["id"],
                    "date": sl["date"],
                    "description": sl["description"],
                    "amount": sl["amount"],
                    "reference": sl["reference"],
                    "matched_entry_id": sl["matched_entry_id"],
                    "status": sl["status"],
                })
            bank_statements.append({
                "id": s["id"],
                "account_code": s["account_code"],
                "statement_date": s["statement_date"],
                "opening_balance": s["opening_balance"],
                "closing_balance": s["closing_balance"],
                "currency": s["currency"],
                "lines": stmt_lines,
                "status": s["status"],
                "created_at": s["created_at"],
            })

        # Load budgets with lines
        budget_rows = conn.execute("SELECT * FROM budgets ORDER BY created_at DESC").fetchall()
        budgets = []
        for b in budget_rows:
            bl_rows = conn.execute(
                "SELECT * FROM budget_lines WHERE budget_id = ? ORDER BY id",
                (b["id"],),
            ).fetchall()
            budget_lines = []
            for bl in bl_rows:
                budget_lines.append({
                    "account_code": bl["account_code"],
                    "budgeted_amount": bl["budgeted_amount"],
                    "actual_amount": bl["actual_amount"],
                    "variance": bl["variance"],
                    "variance_pct": bl["variance_pct"],
                })
            budgets.append({
                "id": b["id"],
                "name": b["name"],
                "period_start": b["period_start"],
                "period_end": b["period_end"],
                "status": b["status"],
                "lines": budget_lines,
                "total_budgeted": b["total_budgeted"],
                "total_actual": b["total_actual"],
                "total_variance": b["total_variance"],
                "created_at": b["created_at"],
            })

        # Load fiscal years with periods
        fy_rows = conn.execute("SELECT * FROM fiscal_years ORDER BY start_date").fetchall()
        fiscal_years = []
        for fy in fy_rows:
            fp_rows = conn.execute(
                "SELECT * FROM fiscal_periods WHERE fiscal_year_id = ? ORDER BY start_date",
                (fy["id"],),
            ).fetchall()
            periods = []
            for fp in fp_rows:
                periods.append({
                    "name": fp["name"],
                    "start_date": fp["start_date"],
                    "end_date": fp["end_date"],
                    "status": fp["status"],
                    "period_type": fp["period_type"],
                })
            fiscal_years.append({
                "id": fy["id"],
                "name": fy["name"],
                "start_date": fy["start_date"],
                "end_date": fy["end_date"],
                "status": fy["status"],
                "periods": periods,
                "created_at": fy["created_at"],
            })

        # Load general metadata
        meta_rows = conn.execute("SELECT * FROM metadata").fetchall()
        ledger_metadata = {}
        for m in meta_rows:
            # Skip internal keys
            if m["key"].startswith("_"):
                continue
            try:
                ledger_metadata[m["key"]] = json.loads(m["value"])
            except (json.JSONDecodeError, TypeError):
                ledger_metadata[m["key"]] = m["value"]

        # Store fiscal_years in metadata for compatibility
        if fiscal_years:
            ledger_metadata["fiscal_years"] = fiscal_years

        return LedgerData(
            name=meta["name"],
            base_currency=meta["base_currency"],
            accounts=accounts,
            entries=entries,
            exchange_rates=exchange_rates,
            audit_log=AuditLogData(entries=audit_entries),
            closed_periods=closed_periods,
            bank_statements=bank_statements,
            budgets=budgets,
            metadata=ledger_metadata,
            created_at=datetime.fromisoformat(meta["created_at"]),
            updated_at=datetime.fromisoformat(meta["updated_at"]),
        )

    def save(self, data: LedgerData) -> None:
        """Save ledger data to SQLite.

        Uses upsert semantics — inserts new records and updates existing ones.
        """
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        # Update metadata
        conn.execute(
            "UPDATE ledger_meta SET name = ?, base_currency = ?, updated_at = ? WHERE id = 1",
            (data.name, data.base_currency, now),
        )

        # Upsert accounts
        existing_account_codes = {
            r["code"] for r in conn.execute("SELECT code FROM accounts").fetchall()
        }
        for code, acct in data.accounts.items():
            tags_json = json.dumps(acct.tags) if hasattr(acct, "tags") and acct.tags else "[]"
            metadata_json = json.dumps(acct.metadata) if acct.metadata else "{}"
            if code in existing_account_codes:
                conn.execute(
                    """UPDATE accounts SET name = ?, account_type = ?, currency = ?,
                       description = ?, active = ?, parent_code = ?, metadata = ?,
                       tags = ?, created_at = ? WHERE code = ?""",
                    (acct.name, acct.account_type.value, acct.currency,
                     acct.description, int(acct.active), acct.parent_code,
                     metadata_json, tags_json,
                     acct.created_at.isoformat(), code),
                )
            else:
                conn.execute(
                    """INSERT INTO accounts (code, name, account_type, currency, description,
                       active, parent_code, metadata, tags, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (code, acct.name, acct.account_type.value, acct.currency,
                     acct.description, int(acct.active), acct.parent_code,
                     metadata_json, tags_json,
                     acct.created_at.isoformat()),
                )

        # Delete accounts not in data
        current_codes = set(data.accounts.keys())
        deleted_codes = existing_account_codes - current_codes
        for code in deleted_codes:
            conn.execute("DELETE FROM accounts WHERE code = ?", (code,))

        # Upsert journal entries with lines
        existing_entry_ids = {
            r["id"] for r in conn.execute("SELECT id FROM journal_entries").fetchall()
        }
        for entry in data.entries:
            tags_json = json.dumps(entry.tags) if entry.tags else "[]"
            metadata_json = json.dumps(entry.metadata) if entry.metadata else "{}"

            if entry.id in existing_entry_ids:
                conn.execute(
                    """UPDATE journal_entries SET description = ?, timestamp = ?,
                       tags = ?, reconciled = ?, metadata = ? WHERE id = ?""",
                    (entry.description, entry.timestamp.isoformat(),
                     tags_json, int(entry.reconciled), metadata_json, entry.id),
                )
                # Delete old lines and re-insert
                conn.execute("DELETE FROM journal_lines WHERE entry_id = ?", (entry.id,))
            else:
                conn.execute(
                    """INSERT INTO journal_entries (id, description, timestamp, tags, reconciled, metadata)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (entry.id, entry.description, entry.timestamp.isoformat(),
                     tags_json, int(entry.reconciled), metadata_json),
                )

            for line in entry.lines:
                conn.execute(
                    """INSERT INTO journal_lines (entry_id, account_code, debit, credit, line_description)
                       VALUES (?, ?, ?, ?, ?)""",
                    (entry.id, line.account_code, line.debit, line.credit, line.description),
                )

        # Delete entries not in data
        current_entry_ids = {e.id for e in data.entries}
        deleted_entry_ids = existing_entry_ids - current_entry_ids
        for eid in deleted_entry_ids:
            conn.execute("DELETE FROM journal_entries WHERE id = ?", (eid,))

        # Upsert exchange rates (full replace)
        conn.execute("DELETE FROM exchange_rates")
        for rate in data.exchange_rates:
            conn.execute(
                """INSERT INTO exchange_rates (from_currency, to_currency, rate, timestamp, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (rate.from_currency, rate.to_currency, rate.rate,
                 rate.timestamp.isoformat(), rate.source),
            )

        # Upsert audit log
        existing_audit_ids = {
            r["id"] for r in conn.execute("SELECT id FROM audit_log").fetchall()
        }
        if data.audit_log and data.audit_log.entries:
            for a in data.audit_log.entries:
                aid = a.get("id", "")
                if aid and aid not in existing_audit_ids:
                    details_json = json.dumps(a.get("details", {}))
                    before_json = json.dumps(a["before"]) if a.get("before") else None
                    after_json = json.dumps(a["after"]) if a.get("after") else None
                    conn.execute(
                        """INSERT OR IGNORE INTO audit_log (id, action, actor, timestamp, details, before_state, after_state)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (aid, a.get("action", ""), a.get("actor", "system"),
                         a.get("timestamp", now), details_json, before_json, after_json),
                    )

        # Upsert closed periods (full replace)
        conn.execute("DELETE FROM closed_periods")
        for cp in data.closed_periods:
            conn.execute(
                """INSERT INTO closed_periods (closing_entry_id, closed_at, net_income,
                   revenue_accounts_closed, expense_accounts_closed, retained_earnings_account)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (cp.get("closing_entry_id", ""), cp.get("closed_at", now),
                 cp.get("net_income", 0.0),
                 json.dumps(cp.get("revenue_accounts_closed", [])),
                 json.dumps(cp.get("expense_accounts_closed", [])),
                 cp.get("retained_earnings_account", "")),
            )

        # Upsert bank statements with lines
        conn.execute("DELETE FROM bank_statement_lines")
        conn.execute("DELETE FROM bank_statements")
        for stmt in data.bank_statements:
            conn.execute(
                """INSERT INTO bank_statements (id, account_code, statement_date, opening_balance,
                   closing_balance, currency, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (stmt.get("id", ""), stmt.get("account_code", ""),
                 stmt.get("statement_date"), stmt.get("opening_balance", 0.0),
                 stmt.get("closing_balance", 0.0), stmt.get("currency", "USD"),
                 stmt.get("status", "open"), stmt.get("created_at", now)),
            )
            for sl in stmt.get("lines", []):
                conn.execute(
                    """INSERT INTO bank_statement_lines (id, statement_id, date, description,
                       amount, reference, matched_entry_id, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (sl.get("id", ""), stmt.get("id", ""),
                     sl.get("date"), sl.get("description", ""),
                     sl.get("amount", 0.0), sl.get("reference", ""),
                     sl.get("matched_entry_id"), sl.get("status", "unmatched")),
                )

        # Upsert budgets with lines
        conn.execute("DELETE FROM budget_lines")
        conn.execute("DELETE FROM budgets")
        for b in data.budgets:
            conn.execute(
                """INSERT INTO budgets (id, name, period_start, period_end, status,
                   total_budgeted, total_actual, total_variance, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (b.get("id", ""), b.get("name", ""),
                 b.get("period_start"), b.get("period_end"),
                 b.get("status", "draft"),
                 b.get("total_budgeted", 0.0), b.get("total_actual", 0.0),
                 b.get("total_variance", 0.0), b.get("created_at", now)),
            )
            for bl in b.get("lines", []):
                conn.execute(
                    """INSERT INTO budget_lines (budget_id, account_code, budgeted_amount,
                       actual_amount, variance, variance_pct)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (b.get("id", ""), bl.get("account_code", ""),
                     bl.get("budgeted_amount", 0.0), bl.get("actual_amount", 0.0),
                     bl.get("variance", 0.0), bl.get("variance_pct", 0.0)),
                )

        # Upsert fiscal years with periods
        conn.execute("DELETE FROM fiscal_periods")
        conn.execute("DELETE FROM fiscal_years")
        fy_data = data.metadata.get("fiscal_years", []) if data.metadata else []
        for fy in fy_data:
            conn.execute(
                """INSERT INTO fiscal_years (id, name, start_date, end_date, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (fy.get("id", ""), fy.get("name", ""),
                 fy.get("start_date", now), fy.get("end_date", now),
                 fy.get("status", "open"), fy.get("created_at", now)),
            )
            for fp in fy.get("periods", []):
                conn.execute(
                    """INSERT INTO fiscal_periods (fiscal_year_id, name, start_date, end_date, status, period_type)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (fy.get("id", ""), fp.get("name", ""),
                     fp.get("start_date", now), fp.get("end_date", now),
                     fp.get("status", "open"), fp.get("period_type", "month")),
                )

        # Save metadata (skip internal keys)
        if data.metadata:
            for key, value in data.metadata.items():
                if key == "fiscal_years":
                    continue  # Stored in dedicated tables
                value_json = json.dumps(value) if not isinstance(value, str) else value
                conn.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                    (key, value_json),
                )

        conn.commit()

    def delete(self) -> None:
        """Delete the ledger database file."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self.filepath.exists():
            self.filepath.unlink()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Fast Query Methods ──────────────────────────────────────

    def search_entries_by_description(self, query: str, limit: int = 50) -> list[dict]:
        """Search journal entries by description using LIKE.

        Returns a list of dicts with entry data (without full line details).
        Much faster than loading all entries into memory.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT je.id, je.description, je.timestamp, je.tags, je.reconciled,
                      SUM(jl.debit) as total_debits, SUM(jl.credit) as total_credits
               FROM journal_entries je
               JOIN journal_lines jl ON je.id = jl.entry_id
               WHERE je.description LIKE ?
               GROUP BY je.id
               ORDER BY je.timestamp DESC
               LIMIT ?""",
            (f"%{query}%", limit),
        ).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "description": r["description"],
                "timestamp": r["timestamp"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "reconciled": bool(r["reconciled"]),
                "total_debits": round(r["total_debits"], 2),
                "total_credits": round(r["total_credits"], 2),
            })
        return results

    def get_account_balance_fast(self, account_code: str) -> Optional[dict]:
        """Compute an account balance directly from the database.

        Much faster than loading all entries and iterating in Python.
        """
        conn = self._get_conn()
        row = conn.execute(
            """SELECT SUM(jl.debit) as debit_total, SUM(jl.credit) as credit_total
               FROM journal_lines jl
               WHERE jl.account_code = ?""",
            (account_code,),
        ).fetchone()

        if row is None:
            return None

        # Get account info
        acct = conn.execute("SELECT * FROM accounts WHERE code = ?", (account_code,)).fetchone()
        if acct is None:
            return None

        debit_total = row["debit_total"] or 0.0
        credit_total = row["credit_total"] or 0.0

        return {
            "account_code": account_code,
            "account_name": acct["name"],
            "account_type": acct["account_type"],
            "currency": acct["currency"],
            "debit_total": round(debit_total, 2),
            "credit_total": round(credit_total, 2),
            "raw_balance": round(debit_total - credit_total, 2),
        }
