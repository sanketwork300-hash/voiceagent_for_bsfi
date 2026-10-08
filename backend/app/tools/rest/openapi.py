"""Import an OpenAPI 3.x document into governed tool definitions.

Governance comes from vendor extensions on each operation (defaults are conservative):
  x-bfsi-risk-level: LOW|MEDIUM|HIGH|CRITICAL      x-bfsi-min-auth: <AuthState>
  x-bfsi-requires-confirmation: bool               x-bfsi-injected: {param: context_key}
  x-bfsi-intents: [Intent, ...]                    x-bfsi-internal: bool
  x-bfsi-confirmation-template: str                x-bfsi-tool: false  (exclude operation)
Scheduling metadata (merged conservatively by `resolve_execution`: a POST can never become a parallel read):
  x-bfsi-operation-type: READ|WRITE|VERIFY         x-bfsi-side-effect: NONE|ACCOUNT_READ|ACCOUNT_MUTATION|FINANCIAL_MUTATION|EXTERNAL_SIDE_EFFECT
  x-bfsi-parallel-safe: bool                       x-bfsi-idempotent: bool
  x-bfsi-concurrency-group: str                    x-bfsi-depends-on: [tool, ...]     x-bfsi-max-concurrency: int
"""

from __future__ import annotations

import re
from typing import Any

from app.domain import AuthState, Intent, RiskLevel
from app.tools.schemas import ToolDefinition, ToolSource, resolve_execution

_METHODS = ("get", "post", "put", "patch", "delete")
_CREDENTIAL_HEADERS = {"authorization", "x-api-key", "api-key", "apikey", "cookie", "proxy-authorization"}
_EXEC_EXT = {"x-bfsi-operation-type": "operation_type", "x-bfsi-side-effect": "side_effect", "x-bfsi-parallel-safe": "parallel_safe",
             "x-bfsi-idempotent": "idempotent", "x-bfsi-concurrency-group": "concurrency_group",
             "x-bfsi-depends-on": "depends_on", "x-bfsi-max-concurrency": "max_concurrency"}


def _snake(s: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s)
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower().strip("_").replace("__", "_")


def _resolve(spec: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if depth > 12:
        return node
    if isinstance(node, dict):
        if "$ref" in node:
            target: Any = spec
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target[part]
            return _resolve(spec, target, depth + 1)
        return {k: _resolve(spec, v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(spec, v, depth + 1) for v in node]
    return node


def import_openapi(spec: dict[str, Any], *, integration_id: str) -> list[ToolDefinition]:
    if not str(spec.get("openapi", "")).startswith("3"):
        raise ValueError("only OpenAPI 3.x documents are supported")
    tools: list[ToolDefinition] = []
    for path, item in (spec.get("paths") or {}).items():
        shared = item.get("parameters", [])
        for method in _METHODS:
            op = item.get(method)
            if not op or op.get("x-bfsi-tool") is False:
                continue
            name = _snake(op.get("operationId") or f"{method}_{path}")
            props: dict[str, Any] = {}
            required: list[str] = []
            pmap: dict[str, str] = {}
            for p in _resolve(spec, shared + op.get("parameters", [])):
                if p.get("in") not in ("path", "query", "header"):
                    continue
                if p.get("in") == "header" and str(p.get("name", "")).lower() in _CREDENTIAL_HEADERS:
                    continue  # credentials come from the integration, never from model arguments
                schema = dict(p.get("schema") or {"type": "string"})
                if p.get("description"):
                    schema["description"] = p["description"]
                props[p["name"]] = schema
                pmap[p["name"]] = p["in"]
                if p.get("required") or p.get("in") == "path":
                    required.append(p["name"])
            body = _resolve(spec, (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema"))
            if body and body.get("type", "object") == "object":
                for k, v in (body.get("properties") or {}).items():
                    props[k] = v
                    pmap[k] = "body"
                required += [r for r in body.get("required", []) if r not in required]
            risk = RiskLevel(op.get("x-bfsi-risk-level", "MEDIUM" if method == "get" else "HIGH"))
            binding = {"method": method.upper(), "path": path, "parameter_map": pmap, "operation_id": op.get("operationId")}
            declared = {field: op[ext] for ext, field in _EXEC_EXT.items() if ext in op}
            idempotent = bool(op.get("x-bfsi-idempotent", method == "get"))
            tools.append(ToolDefinition(
                name=name,
                description=(op.get("description") or op.get("summary") or name).strip(),
                input_schema={"type": "object", "properties": props, "required": required},
                risk_level=risk,
                min_auth_state=AuthState(op.get("x-bfsi-min-auth", AuthState.FULLY_AUTHENTICATED.value)),
                requires_confirmation=bool(op.get("x-bfsi-requires-confirmation", method != "get")),
                source=ToolSource.OPENAPI,
                integration_id=integration_id,
                binding=binding,
                injected_params=op.get("x-bfsi-injected", {}),
                intents=[Intent(i) for i in op.get("x-bfsi-intents", [])],
                confirmation_template=op.get("x-bfsi-confirmation-template"),
                idempotent=idempotent,
                internal=bool(op.get("x-bfsi-internal", False)),
                execution=resolve_execution(source=ToolSource.OPENAPI, binding=binding, idempotent=idempotent,
                                            internal=bool(op.get("x-bfsi-internal", False)), declared=declared,
                                            default_group=integration_id),
            ))
    return tools
