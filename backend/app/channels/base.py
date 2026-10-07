"""Channel abstraction. Channels only translate I/O; all business logic lives in the AgentRuntime."""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.runtime import AgentRuntime
from app.agents.state import SessionState
from app.domain import (
    AgentRequest,
    AgentResponse,
    Channel,
    OutputModality,
    RuntimeEvent,
)


@dataclass
class ChannelInput:
    text: str
    language_hint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class InteractionChannel(ABC):
    channel: Channel
    modality: OutputModality = OutputModality.TEXT

    @abstractmethod
    async def receive(self) -> ChannelInput | None:
        """Next user input (None when the channel closes)."""

    @abstractmethod
    async def send(self, event: RuntimeEvent) -> None:
        """Deliver a runtime event in the channel's native form (JSON frame, TTS audio, ...)."""

    @abstractmethod
    async def interrupt(self) -> None:
        """Stop the in-flight response (user barge-in / explicit cancel)."""


def build_request(state: SessionState, text: str, *, channel: Channel, modality: OutputModality,
                  language_hint: str | None = None, metadata: dict[str, Any] | None = None) -> AgentRequest:
    """The one place channel input becomes the normalized runtime request."""
    return AgentRequest(
        tenant_id=state.tenant_id, session_id=state.session_id, user_id=state.customer_id, channel=channel,
        language=language_hint or state.language, message=text, authentication_state=state.authentication_state,
        output_modality=modality, metadata=metadata or {},
    )


class TurnRunner:
    """Runs one runtime turn as a cancellable task, forwarding events to a sink."""

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime
        self._task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run(self, request: AgentRequest, sink: Callable[[RuntimeEvent], Awaitable[None]]) -> AgentResponse | None:
        async def _go() -> AgentResponse | None:
            response = None
            async for ev in self.runtime.stream(request):
                await sink(ev)
                if ev.type == "message.completed":
                    response = ev.response
            return response

        self._task = asyncio.create_task(_go())
        try:
            return await self._task
        except asyncio.CancelledError:
            if self._task.cancelled():
                return None
            raise

    async def cancel(self) -> bool:
        if self.busy:
            assert self._task is not None
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            return True
        return False
