from app.domain import AuthState, RiskLevel
from app.tools.mcp.discovery import governance_from_annotations
from app.tools.rest.openapi import import_openapi
from app.tools.schemas import ToolDefinition, ToolSource

SPEC = {
    "openapi": "3.1.0",
    "paths": {
        "/v1/customers/{customer_id}/loans": {"get": {
            "operationId": "getLoanDetails", "summary": "Loans",
            "parameters": [{"name": "customer_id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "x-bfsi-risk-level": "MEDIUM", "x-bfsi-injected": {"customer_id": "customer_id"}}},
        "/v1/transfers": {"post": {
            "operationId": "create_transfer",
            "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/T"}}}}}},
        "/internal": {"get": {"operationId": "hidden", "x-bfsi-tool": False}},
    },
    "components": {"schemas": {"T": {"type": "object", "required": ["amount"], "properties": {"amount": {"type": "number"}}}}},
}


def test_openapi_import_governance_and_defaults():
    tools = {t.name: t for t in import_openapi(SPEC, integration_id="i1")}
    assert set(tools) == {"get_loan_details", "create_transfer"}
    loan = tools["get_loan_details"]
    assert loan.risk_level == RiskLevel.MEDIUM and loan.binding["parameter_map"] == {"customer_id": "path"}
    assert "customer_id" not in loan.llm_schema()["properties"]  # injected params are hidden from the LLM
    t = tools["create_transfer"]
    assert t.risk_level == RiskLevel.HIGH and t.requires_confirmation  # conservative defaults for writes
    assert t.input_schema["required"] == ["amount"]


def test_llm_view_is_transport_agnostic():
    a = ToolDefinition(name="x", description="d", source=ToolSource.MCP, injected_params={"customer_id": "customer_id"},
                       input_schema={"type": "object", "properties": {"customer_id": {"type": "string"}, "y": {"type": "string"}},
                                     "required": ["customer_id"]})
    b = a.model_copy(update={"source": ToolSource.OPENAPI})
    assert a.llm_view() == b.llm_view()
    assert set(a.llm_view()) == {"name", "description", "input_schema", "risk_level"}
    assert a.llm_schema()["required"] == []


def test_mcp_annotations_cannot_lower_risk_below_medium():
    g = governance_from_annotations({"name": "t", "annotations": {"readOnlyHint": False, "destructiveHint": True},
                                     "_meta": {"bfsi": {"risk_level": "LOW"}}, "inputSchema": {"properties": {"customer_id": {}}}})
    assert g["risk_level"] == "MEDIUM" and g["injected_params"] == {"customer_id": "customer_id"}
    g2 = governance_from_annotations({"name": "t", "annotations": {"readOnlyHint": True}})
    assert g2["risk_level"] == "MEDIUM" and g2["min_auth_state"] == AuthState.FULLY_AUTHENTICATED.value


async def test_tenant_repository_scopes_every_query(container, tenant):
    import pytest

    from app.database.models import Agent
    from app.database.repositories import TenantIsolationError, TenantRepository

    async with container.db.session() as s:
        mine = await TenantRepository(s, Agent, tenant.id).list()
        assert mine and all(a.tenant_id == tenant.id for a in mine)
        assert await TenantRepository(s, Agent, "other-tenant").get(mine[0].id) is None
        with pytest.raises(TenantIsolationError):
            await TenantRepository(s, Agent, "other-tenant").add(Agent(name="x", tenant_id=tenant.id))
