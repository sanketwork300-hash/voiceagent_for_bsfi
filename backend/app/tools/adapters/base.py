"""Custom integration adapters for systems that are neither REST/OpenAPI nor MCP (SOAP, ISO 8583, MQ, ...).

An adapter is registered by name and referenced from an Integration (`config.adapter`). Its operations
surface as normal governed tools — the LLM cannot tell an adapter from REST or MCP.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.integrations.credentials import CredentialManager, IntegrationConfig
from app.tools.schemas import ToolContext, ToolDefinition, ToolExecutionError


class BankAdapter(ABC):
    name: ClassVar[str]

    def __init__(self, config: IntegrationConfig) -> None:
        self.config = config

    @abstractmethod
    async def call(self, operation: str, arguments: dict[str, Any], ctx: ToolContext) -> Any: ...

    async def health(self) -> bool:
        return True


ADAPTERS: dict[str, type[BankAdapter]] = {}


def register_adapter(cls: type[BankAdapter]) -> type[BankAdapter]:
    ADAPTERS[cls.name] = cls
    return cls


class AdapterToolExecutor:
    def __init__(self, credentials: CredentialManager) -> None:
        self.credentials = credentials
        self._instances: dict[tuple[str, str], BankAdapter] = {}

    async def execute(self, tool: ToolDefinition, args: dict[str, Any], ctx: ToolContext) -> Any:
        if not tool.integration_id:
            raise ToolExecutionError("adapter tool has no integration")
        key = (ctx.tenant_id, tool.integration_id)
        if key not in self._instances:
            cfg = await self.credentials.get(ctx.tenant_id, tool.integration_id)
            cls = ADAPTERS.get(cfg.config.get("adapter", ""))
            if cls is None:
                raise ToolExecutionError("adapter not registered")
            self._instances[key] = cls(cfg)
        return await self._instances[key].call(tool.binding.get("operation", tool.name), args, ctx)
