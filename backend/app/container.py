"""Composition root. Builds one shared AgentRuntime (and everything under it) from Settings.

The FastAPI app (chat), the LiveKit worker (voice) and the evaluation runner all construct this same
container, so every channel runs identical runtime, RAG, tool gateway, policy and security code.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.agents.execution import ExecutionEngine
from app.agents.execution.concurrency import SlotPool
from app.agents.execution.templates import apply_bound_params
from app.agents.orchestrator import Orchestrator
from app.agents.planner import IntentClassifier, Planner
from app.agents.runtime import AgentDirectory, AgentRuntime
from app.auth.authentication import CustomerAuthService, StaffAuthService
from app.auth.jwt import JWTService
from app.auth.oauth import ClientCredentialsTokenProvider
from app.channels.voice.session import VoiceSessionService
from app.config import Settings
from app.database.session import Database
from app.escalation.handoff import HandoffService
from app.escalation.human_agent import HumanAgentDesk
from app.integrations.credentials import CredentialManager
from app.integrations.manager import IntegrationManager
from app.knowledge.documents import DocumentService
from app.knowledge.embeddings.provider import (
    EmbeddingProvider,
    HashingEmbeddings,
    OpenAICompatibleEmbeddings,
)
from app.knowledge.ingestion.pipeline import IngestionPipeline
from app.knowledge.rag import RAGEngine
from app.knowledge.retrieval.base import KnowledgeStore
from app.knowledge.retrieval.hybrid import HybridRetriever
from app.knowledge.retrieval.memory import InMemoryKnowledgeStore
from app.knowledge.retrieval.reranker import (
    HTTPCrossEncoderReranker,
    LexicalReranker,
    NoopReranker,
    Reranker,
)
from app.llm.factory import create_llm_provider
from app.policies.approval import ApprovalService
from app.policies.engine import PolicyEngine
from app.security.audit import AuditLogger
from app.security.secrets import SecretBox
from app.sessions.manager import (
    InMemoryStateStore,
    RedisStateStore,
    SessionManager,
    StateStore,
)
from app.sessions.memory import ConversationMemory
from app.tools.adapters.base import AdapterToolExecutor
from app.tools.mcp.client import MCPToolExecutor
from app.tools.registry import REQUEST_HANDOFF, SEARCH_KNOWLEDGE, ToolRegistry
from app.tools.rest.client import RestToolExecutor
from app.tools.router import GatewayLimits, ToolGateway
from app.tools.schemas import ToolContext, ToolSource

log = logging.getLogger(__name__)


class Container:
    def __init__(self, settings: Settings, *, http_transport: httpx.AsyncBaseTransport | None = None,
                 store: StateStore | None = None, knowledge_store: KnowledgeStore | None = None,
                 llm: Any = None, embeddings: EmbeddingProvider | None = None) -> None:
        s = self.settings = settings
        self.http_transport = http_transport  # tests route integration HTTP to in-process mock bank apps
        self.db = Database(s.database_url, echo=s.database_echo)
        self.store = store or (RedisStateStore(s.redis_url) if s.redis_url else InMemoryStateStore())
        self.jwt = JWTService(s.jwt_secret, s.jwt_algorithm)
        self.audit = AuditLogger(self.db)
        self.secret_box = SecretBox(s.encryption_key, s.jwt_secret)
        self.oauth = ClientCredentialsTokenProvider(transport=http_transport)
        self.credentials = CredentialManager(self.db, self.secret_box, self.oauth)

        # Capacity pools (cluster-wide with Redis): LLM requests, active calls, STT/TTS streams
        self.slots = SlotPool(self.store.redis if isinstance(self.store, RedisStateStore) else None, prefix="capacity")

        # LLM
        self.llm = llm or create_llm_provider(s, self.slots)

        # Knowledge
        self.embeddings = embeddings or (
            OpenAICompatibleEmbeddings(s.embedding_base_url, s.embedding_api_key, s.embedding_model, s.embedding_dimensions)
            if s.embedding_provider == "openai" else HashingEmbeddings(s.embedding_dimensions))
        if knowledge_store is not None:
            self.knowledge_store = knowledge_store
        elif s.knowledge_backend == "elasticsearch":
            from app.knowledge.retrieval.elasticsearch import (
                ElasticsearchKnowledgeStore,
            )

            self.knowledge_store = ElasticsearchKnowledgeStore(s.elasticsearch_url, s.elasticsearch_index, s.elasticsearch_api_key)
        else:
            self.knowledge_store = InMemoryKnowledgeStore()
        reranker: Reranker = {"lexical": LexicalReranker(), "none": NoopReranker()}.get(s.reranker_provider) or (
            HTTPCrossEncoderReranker(s.reranker_url) if s.reranker_url else LexicalReranker())
        self.retriever = HybridRetriever(self.knowledge_store, self.embeddings, reranker)
        self.rag = RAGEngine(self.retriever, top_k=s.rag_top_k, candidate_k=s.rag_candidate_k)
        self.ingestion = IngestionPipeline(self.knowledge_store, self.embeddings)
        self.documents = DocumentService(self.db, self.ingestion, self.knowledge_store, s.document_storage_dir)

        # Tools, policy
        self.registry = ToolRegistry(self.db)
        self.registry.add_hook(apply_bound_params)  # workflow-bound params are hidden from the LLM
        self.registry.register_builtin(SEARCH_KNOWLEDGE, self._search_knowledge)
        self.registry.register_builtin(REQUEST_HANDOFF, self._request_handoff)
        self.approvals = ApprovalService(self.db)
        self.policy = PolicyEngine(self.db, self.approvals)
        self.rest_executor = RestToolExecutor(self.credentials, transport=http_transport)
        self.mcp_executor = MCPToolExecutor(self.db, self.credentials, http_transport=http_transport)
        self.gateway = ToolGateway(self.registry, self.policy, self.db, self.audit, {
            ToolSource.REST: self.rest_executor,
            ToolSource.MCP: self.mcp_executor,
            ToolSource.ADAPTER: AdapterToolExecutor(self.credentials),
        }, GatewayLimits(default_tool_timeout=s.default_tool_timeout, read_retry_count=s.read_retry_count,
                         write_retry_count=s.write_retry_count))
        self.engine = ExecutionEngine(s, self.db, self.gateway,
                                      redis_client=self.store.redis if isinstance(self.store, RedisStateStore) else None)
        self.integrations = IntegrationManager(self.db, self.credentials, self.registry, self.mcp_executor, http_transport)

        # Sessions, auth, escalation
        self.sessions = SessionManager(self.db, self.store, s.session_idle_timeout_seconds, lock_ttl=s.session_lock_ttl,
                                       lock_wait=s.session_lock_wait)
        self.memory = ConversationMemory(self.db, s.history_window_messages)
        self.staff_auth = StaffAuthService(self.db)
        self.customer_auth = CustomerAuthService(self.db, self.gateway, self.audit, s.customer_assertion_secret, s.max_auth_failures)
        self.handoff = HandoffService(self.db, self.memory, self.llm, self.store, self.audit)
        self.desk = HumanAgentDesk(self.db, self.sessions, self.memory, self.store, self.audit)

        # Runtime
        self.directory = AgentDirectory(self.db)
        self.planner = Planner(IntentClassifier(self.llm, use_llm=s.intent_classifier == "llm"))
        self.orchestrator = Orchestrator(
            settings=s, sessions=self.sessions, memory=self.memory, llm=self.llm, planner=self.planner,
            registry=self.registry, gateway=self.gateway, auth=self.customer_auth, handoff=self.handoff,
            approvals=self.approvals, audit=self.audit, profiles=self.directory, engine=self.engine)
        self.runtime = AgentRuntime(self.orchestrator)
        self.voice = VoiceSessionService(s, self.sessions, db=self.db, runtime=self.runtime, audit=self.audit,
                                         slots=self.slots, auth=self.customer_auth)

    async def _search_knowledge(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        query = str(args.get("query", ""))[:500]
        results = await self.rag.search(tenant_id=ctx.tenant_id, query=query, auth_state=ctx.auth_state,
                                        product=args.get("product"), channel=ctx.channel.value)
        if not results and args.get("product"):  # a guessed product filter must not hide the answer
            results = await self.rag.search(tenant_id=ctx.tenant_id, query=query, auth_state=ctx.auth_state, channel=ctx.channel.value)
        return {"results": [r.model_dump() for r in results]}

    @staticmethod
    async def _request_handoff(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"accepted": True, "reason": args.get("reason")}

    async def startup(self, create_schema: bool = False) -> None:
        if create_schema:
            await self.db.create_all()
        try:
            await self.knowledge_store.ensure_ready(self.embeddings.dimensions)
        except Exception:
            log.exception("knowledge store not ready; RAG will be unavailable until it is")

    async def shutdown(self) -> None:
        for closer in (self.mcp_executor.aclose, self.rest_executor.aclose, self.knowledge_store.aclose,
                       self.llm.aclose, self.store.aclose, self.db.dispose):
            try:
                await closer()
            except Exception:
                log.exception("shutdown step failed")
