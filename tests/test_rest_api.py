"""Tests for the REST API server."""

import pytest
from pathlib import Path

from agent_ledger.storage import Storage
from agent_ledger.ledger import Ledger
from agent_ledger.models import AccountType, JournalLine
from agent_ledger.rest_api import create_app


@pytest.fixture
def app(tmp_path):
    """Create a FastAPI test app."""
    filepath = tmp_path / "test_ledger.json"
    return create_app(str(filepath))


@pytest.fixture
def client(app):
    """Create a test client."""
    from httpx import AsyncClient, ASGITransport
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def initialized_client(client):
    """Client with initialized ledger."""
    async def _init():
        from httpx import ASGITransport, AsyncClient
        async with client as c:
            await c.post("/init", json={"name": "Test", "base_currency": "USD"})
    return client


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["version"] == "0.6.0"


class TestInitLedger:
    @pytest.mark.asyncio
    async def test_init(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/init", json={"name": "My Ledger", "base_currency": "EUR"})
            assert response.status_code == 201
            data = response.json()
            assert data["name"] == "My Ledger"
            assert data["base_currency"] == "EUR"

    @pytest.mark.asyncio
    async def test_init_already_exists(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={"name": "Test"})
            response = await client.post("/init", json={"name": "Test2"})
            assert response.status_code == 409


class TestAccountCRUD:
    @pytest.mark.asyncio
    async def test_create_and_list_accounts(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Init
            await client.post("/init", json={})
            
            # Create account
            response = await client.post("/accounts", json={
                "code": "cash",
                "name": "Cash",
                "account_type": "asset",
                "currency": "USD",
            })
            assert response.status_code == 201
            data = response.json()
            assert data["code"] == "cash"
            assert data["name"] == "Cash"
            assert data["balance"] == 0.0
            
            # List accounts
            response = await client.get("/accounts")
            assert response.status_code == 200
            accounts = response.json()
            assert len(accounts) == 1
            assert accounts[0]["code"] == "cash"

    @pytest.mark.asyncio
    async def test_get_account(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            
            response = await client.get("/accounts/cash")
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == "cash"
            assert "balance" in data

    @pytest.mark.asyncio
    async def test_update_account(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            
            response = await client.patch("/accounts/cash", json={"name": "Cash on Hand"})
            assert response.status_code == 200
            assert response.json()["name"] == "Cash on Hand"

    @pytest.mark.asyncio
    async def test_delete_account(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            
            response = await client.delete("/accounts/cash")
            assert response.status_code == 200
            assert response.json()["deleted"] is True

    @pytest.mark.asyncio
    async def test_account_not_found(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            
            response = await client.get("/accounts/nonexistent")
            assert response.status_code == 404


class TestJournalEntries:
    @pytest.mark.asyncio
    async def test_post_and_list_entries(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            
            # Post entry
            response = await client.post("/entries", json={
                "description": "Sale",
                "lines": [
                    {"account_code": "cash", "debit": 1000.0, "credit": 0.0},
                    {"account_code": "revenue", "debit": 0.0, "credit": 1000.0},
                ],
            })
            assert response.status_code == 201
            data = response.json()
            assert data["description"] == "Sale"
            
            # List entries
            response = await client.get("/entries")
            assert response.status_code == 200
            entries = response.json()
            assert len(entries) == 1

    @pytest.mark.asyncio
    async def test_reconcile_entry(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            
            entry_resp = await client.post("/entries", json={
                "description": "Sale",
                "lines": [
                    {"account_code": "cash", "debit": 500.0, "credit": 0.0},
                    {"account_code": "revenue", "debit": 0.0, "credit": 500.0},
                ],
            })
            entry_id = entry_resp.json()["id"]
            
            response = await client.post(f"/entries/{entry_id}/reconcile")
            assert response.status_code == 200
            assert response.json()["status"] == "reconciled"

    @pytest.mark.asyncio
    async def test_reverse_entry(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            
            entry_resp = await client.post("/entries", json={
                "description": "Sale",
                "lines": [
                    {"account_code": "cash", "debit": 500.0, "credit": 0.0},
                    {"account_code": "revenue", "debit": 0.0, "credit": 500.0},
                ],
            })
            entry_id = entry_resp.json()["id"]
            
            response = await client.post(f"/entries/{entry_id}/reverse", json={"reason": "Error"})
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "reversed"
            assert data["original_entry_id"] == entry_id

    @pytest.mark.asyncio
    async def test_batch_reconcile(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            
            e1 = await client.post("/entries", json={
                "description": "Sale 1",
                "lines": [{"account_code": "cash", "debit": 100.0, "credit": 0.0}, {"account_code": "revenue", "debit": 0.0, "credit": 100.0}],
            })
            e2 = await client.post("/entries", json={
                "description": "Sale 2",
                "lines": [{"account_code": "cash", "debit": 200.0, "credit": 0.0}, {"account_code": "revenue", "debit": 0.0, "credit": 200.0}],
            })
            
            response = await client.post("/entries/batch-reconcile", json={
                "entry_ids": [e1.json()["id"], e2.json()["id"]],
            })
            assert response.status_code == 200
            results = response.json()["results"]
            assert all(r["status"] == "reconciled" for r in results)


class TestReports:
    @pytest.mark.asyncio
    async def test_trial_balance(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            await client.post("/entries", json={
                "description": "Sale",
                "lines": [{"account_code": "cash", "debit": 500.0, "credit": 0.0}, {"account_code": "revenue", "debit": 0.0, "credit": 500.0}],
            })
            
            response = await client.get("/reports/trial-balance")
            assert response.status_code == 200
            data = response.json()
            assert data["is_balanced"] is True
            assert data["total_debits"] == 500.0

    @pytest.mark.asyncio
    async def test_trial_balance_with_date(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            await client.post("/entries", json={
                "description": "Sale",
                "lines": [{"account_code": "cash", "debit": 500.0, "credit": 0.0}, {"account_code": "revenue", "debit": 0.0, "credit": 500.0}],
            })
            
            response = await client.get("/reports/trial-balance?as_of=2099-12-31")
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_income_statement_with_date_range(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            await client.post("/entries", json={
                "description": "Sale",
                "lines": [{"account_code": "cash", "debit": 500.0, "credit": 0.0}, {"account_code": "revenue", "debit": 0.0, "credit": 500.0}],
            })
            
            response = await client.get("/reports/income-statement?from_date=2020-01-01&to_date=2099-12-31")
            assert response.status_code == 200
            data = response.json()
            assert data["net_income"] == 500.0

    @pytest.mark.asyncio
    async def test_balance_sheet(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            await client.post("/entries", json={
                "description": "Sale",
                "lines": [{"account_code": "cash", "debit": 500.0, "credit": 0.0}, {"account_code": "revenue", "debit": 0.0, "credit": 500.0}],
            })
            
            response = await client.get("/reports/balance-sheet")
            assert response.status_code == 200
            data = response.json()
            assert data["total_assets"] == 500.0

    @pytest.mark.asyncio
    async def test_cash_flow(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            await client.post("/entries", json={
                "description": "Sale",
                "lines": [{"account_code": "cash", "debit": 500.0, "credit": 0.0}, {"account_code": "revenue", "debit": 0.0, "credit": 500.0}],
            })
            
            response = await client.get("/reports/cash-flow")
            assert response.status_code == 200


class TestHierarchy:
    @pytest.mark.asyncio
    async def test_get_hierarchy(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            
            response = await client.get("/hierarchy")
            assert response.status_code == 200


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_list_audit(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            
            response = await client.get("/audit")
            assert response.status_code == 200
            entries = response.json()
            assert len(entries) >= 1


class TestExchangeRates:
    @pytest.mark.asyncio
    async def test_add_and_list_rates(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            
            response = await client.post("/exchange-rates", json={
                "from_currency": "USD",
                "to_currency": "EUR",
                "rate": 0.85,
            })
            assert response.status_code == 201
            
            response = await client.get("/exchange-rates")
            assert response.status_code == 200
            rates = response.json()
            assert len(rates) >= 1


class TestPeriodClose:
    @pytest.mark.asyncio
    async def test_close_period(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init", json={})
            await client.post("/accounts", json={"code": "cash", "name": "Cash", "account_type": "asset"})
            await client.post("/accounts", json={"code": "revenue", "name": "Revenue", "account_type": "revenue"})
            await client.post("/entries", json={
                "description": "Sale",
                "lines": [{"account_code": "cash", "debit": 500.0, "credit": 0.0}, {"account_code": "revenue", "debit": 0.0, "credit": 500.0}],
            })
            
            response = await client.post("/period/close", json={"retained_earnings_code": "retained_earnings"})
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "closed"
            assert data["net_income"] == 500.0
