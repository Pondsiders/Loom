"""Trace management for grouping API calls into logical turns.

A "turn" is: user types something → Claude responds (possibly with tool calls) → final response.

The hook generates a trace_id for each user prompt. The Loom uses this to:
1. Create a parent "turn" span when a new trace_id arrives
2. Nest all API call spans under that parent
3. Accumulate text output across spans
4. Finalize when response has no tool_use (turn complete)
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanContext, TraceFlags
from opentelemetry.context import Context

logger = logging.getLogger(__name__)


@dataclass
class ActiveTrace:
    """State for an in-progress trace (user turn)."""

    trace_id: str
    session_id: str
    prompt: str  # What the user actually typed
    start_time_ns: int
    parent_span: Span
    parent_context: Context
    accumulated_text: list[str] = field(default_factory=list)
    span_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


class TraceManager:
    """Manages active traces, grouping API calls into logical turns."""

    def __init__(self, tracer: trace.Tracer):
        self.tracer = tracer
        # Map trace_id -> ActiveTrace
        self._active: dict[str, ActiveTrace] = {}

    def get_or_create_trace(
        self,
        trace_id: str,
        session_id: str,
        prompt: str,
        is_alpha: bool = False,
    ) -> tuple[ActiveTrace, Context]:
        """Get existing trace or create a new one.

        Returns the ActiveTrace and the context to use for child spans.
        """
        if trace_id in self._active:
            active = self._active[trace_id]
            logger.debug(f"Continuing trace {trace_id}, span #{active.span_count + 1}")
            return active, active.parent_context

        # Create new parent span for this turn
        start_time_ns = time.time_ns()

        # Start the parent span
        parent_span = self.tracer.start_span(
            name="turn",
            kind=trace.SpanKind.SERVER,
            start_time=start_time_ns,
        )

        # Set attributes on parent span
        parent_span.set_attribute("openinference.span.kind", "CHAIN")
        parent_span.set_attribute("session.id", session_id)
        # parent_span.set_attribute("input.value", prompt[:2000])
        parent_span.set_attribute("input.mime_type", "text/plain")

        if is_alpha:
            parent_span.set_attribute("metadata", '{"source": "alpha"}')

        # Get the context with this span active
        parent_context = trace.set_span_in_context(parent_span)

        active = ActiveTrace(
            trace_id=trace_id,
            session_id=session_id,
            prompt=prompt,
            start_time_ns=start_time_ns,
            parent_span=parent_span,
            parent_context=parent_context,
        )

        self._active[trace_id] = active
        logger.info(f"Started new trace {trace_id} for session {session_id}")

        return active, parent_context

    def add_span_result(
        self,
        trace_id: str,
        text_output: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record results from an API call span."""
        if trace_id not in self._active:
            logger.warning(f"No active trace for {trace_id}")
            return

        active = self._active[trace_id]
        active.span_count += 1
        active.total_input_tokens += input_tokens
        active.total_output_tokens += output_tokens

        if text_output:
            active.accumulated_text.append(text_output)

        logger.debug(
            f"Trace {trace_id}: span #{active.span_count}, "
            f"tokens +{input_tokens}/{output_tokens}, "
            f"text_len={len(text_output)}"
        )

    def finalize_trace(self, trace_id: str) -> None:
        """Finalize a trace when the turn is complete (no more tool calls)."""
        if trace_id not in self._active:
            logger.warning(f"No active trace to finalize: {trace_id}")
            return

        active = self._active.pop(trace_id)

        # Set final output on parent span
        full_output = "\n\n".join(active.accumulated_text)
        if full_output:
            active.parent_span.set_attribute("output.value", full_output[:4000])
            active.parent_span.set_attribute("output.mime_type", "text/plain")

        # Set token totals
        active.parent_span.set_attribute("llm.token_count.prompt", active.total_input_tokens)
        active.parent_span.set_attribute("llm.token_count.completion", active.total_output_tokens)
        active.parent_span.set_attribute("llm.token_count.total", active.total_input_tokens + active.total_output_tokens)

        # End the parent span
        active.parent_span.set_status(trace.Status(trace.StatusCode.OK))
        active.parent_span.end(end_time=time.time_ns())

        logger.info(
            f"Finalized trace {trace_id}: {active.span_count} spans, "
            f"{active.total_input_tokens}/{active.total_output_tokens} tokens, "
            f"{len(full_output)} chars output"
        )

    def is_trace_active(self, trace_id: str) -> bool:
        """Check if a trace is currently active."""
        return trace_id in self._active
