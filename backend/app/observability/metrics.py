"""Prometheus metrics, labelled by channel so chat and voice can be tracked separately and jointly."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)
_LAT = (0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 13)

# --- common / per channel ---
turn_latency = Histogram("bfsi_turn_latency_seconds", "End-to-end agent turn latency", ["channel"], buckets=_LAT, registry=REGISTRY)
first_token_latency = Histogram("bfsi_time_to_first_token_seconds", "Runtime time-to-first-token", ["channel"], buckets=_LAT, registry=REGISTRY)
llm_latency = Histogram("bfsi_llm_latency_seconds", "LLM call latency", ["channel", "provider", "purpose"], buckets=_LAT, registry=REGISTRY)
llm_tokens = Counter("bfsi_llm_tokens_total", "LLM token usage", ["channel", "provider", "kind"], registry=REGISTRY)
tool_latency = Histogram("bfsi_tool_latency_seconds", "Tool execution latency", ["channel", "tool", "source"], buckets=_LAT, registry=REGISTRY)
tool_calls = Counter("bfsi_tool_calls_total", "Tool calls by outcome", ["channel", "tool", "outcome"], registry=REGISTRY)
rag_latency = Histogram("bfsi_rag_latency_seconds", "RAG retrieval latency", ["channel"], buckets=_LAT, registry=REGISTRY)
rag_results = Histogram("bfsi_rag_results", "Chunks returned per retrieval", ["channel"], buckets=(0, 1, 2, 3, 5, 8, 13), registry=REGISTRY)
policy_decisions = Counter("bfsi_policy_decisions_total", "Policy decisions", ["channel", "decision", "risk"], registry=REGISTRY)
auth_events = Counter("bfsi_auth_events_total", "Customer authentication events", ["channel", "method", "outcome"], registry=REGISTRY)
handoffs = Counter("bfsi_handoffs_total", "Human handoffs", ["channel", "reason"], registry=REGISTRY)
intents = Counter("bfsi_intents_total", "Classified intents", ["channel", "intent"], registry=REGISTRY)
task_outcomes = Counter("bfsi_task_outcomes_total", "Turn outcomes", ["channel", "outcome"], registry=REGISTRY)
ungrounded_answers = Counter("bfsi_ungrounded_answers_total", "Answers with numbers absent from sources/tool output (hallucination signal)", ["channel"], registry=REGISTRY)

# --- voice specific ---
stt_latency = Histogram("bfsi_voice_stt_latency_seconds", "STT final transcript latency", buckets=_LAT, registry=REGISTRY)
tts_latency = Histogram("bfsi_voice_tts_ttfb_seconds", "TTS time-to-first-byte", buckets=_LAT, registry=REGISTRY)
time_to_first_audio = Histogram("bfsi_voice_time_to_first_audio_seconds", "End of user speech -> first agent audio", buckets=_LAT, registry=REGISTRY)
voice_interruptions = Counter("bfsi_voice_interruptions_total", "Barge-ins / interruptions", registry=REGISTRY)
voice_turns = Counter("bfsi_voice_turns_total", "Voice turns", registry=REGISTRY)
voice_calls = Counter("bfsi_voice_calls_total", "Voice call lifecycle (started/rejected/ended)", ["event", "detail"], registry=REGISTRY)
call_duration = Histogram("bfsi_voice_call_duration_seconds", "Call duration", buckets=(10, 30, 60, 120, 300, 600, 1200, 1800), registry=REGISTRY)

# --- orchestration / execution engine ---
tool_execution_mode = Counter("bfsi_tool_execution_mode_total", "Tool steps by scheduling mode (parallel wave vs sequential)", ["mode"], registry=REGISTRY)
tool_timeouts = Counter("bfsi_tool_timeouts_total", "Tool attempts that hit their timeout", ["tool"], registry=REGISTRY)
tool_retries = Counter("bfsi_tool_retries_total", "Automatic tool retries", ["tool", "category"], registry=REGISTRY)
tool_failures = Counter("bfsi_tool_failures_total", "Failed tool calls by category", ["tool", "category"], registry=REGISTRY)
workflow_outcomes = Counter("bfsi_workflow_outcomes_total", "Workflows by terminal/hold status", ["workflow_type", "status"], registry=REGISTRY)
workflow_latency = Histogram("bfsi_workflow_latency_seconds", "Workflow execution latency per turn (avg/p95 via histogram_quantile)",
                             ["workflow_type"], buckets=_LAT, registry=REGISTRY)
verification_outcomes = Counter("bfsi_verification_outcomes_total", "Post-execution verification results", ["tool", "status"], registry=REGISTRY)
duplicate_prevented = Counter("bfsi_financial_duplicate_prevention_total", "Duplicate / unsafe mutation submissions prevented", ["reason"], registry=REGISTRY)
policy_blocks = Counter("bfsi_policy_blocks_total", "Steps or plans blocked by policy", ["scope", "decision"], registry=REGISTRY)
concurrency_limit_hits = Counter("bfsi_concurrency_limit_hits_total", "Tool steps that had to wait for a concurrency slot", ["scope"], registry=REGISTRY)
session_lock_wait = Histogram("bfsi_session_lock_wait_seconds", "Time waiting for the per-session lock", buckets=_LAT, registry=REGISTRY)
session_busy = Counter("bfsi_session_busy_total", "Turns rejected because the session stayed locked", registry=REGISTRY)
session_state_conflicts = Counter("bfsi_session_state_conflicts_total", "Session saves rejected by version check (lost lock)", registry=REGISTRY)
