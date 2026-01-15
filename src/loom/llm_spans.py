"""LLM span creation with OpenInference attributes for Phoenix.

Creates properly-attributed spans that Phoenix can display, filter, and analyze.
"""

import json
import logging
from typing import Any

from opentelemetry import trace

logger = logging.getLogger(__name__)


def create_llm_span(
    tracer: trace.Tracer,
    request_body: dict[str, Any],
    response_body: dict[str, Any] | None,
    status_code: int,
    start_time_ns: int,
    end_time_ns: int,
    session_id: str | None = None,
    is_alpha: bool = False,
    parent_context: Any = None,
) -> None:
    """Create an LLM span with OpenInference attributes.

    This creates a complete, self-contained span with all the attributes
    Phoenix needs to display the LLM call properly.

    If parent_context is provided, the span will be a child of that context's span.
    """
    model = request_body.get("model", "unknown")

    span = tracer.start_span(
        name=f"llm.{model}",
        kind=trace.SpanKind.CLIENT,
        start_time=start_time_ns,
        context=parent_context,
    )

    try:
        # === Required for Phoenix ===
        span.set_attribute("openinference.span.kind", "LLM")

        # === Core LLM attributes ===
        span.set_attribute("llm.system", "anthropic")
        span.set_attribute("llm.model_name", model)

        # gen_ai.* for Parallax routing (sends to Phoenix instead of Logfire)
        span.set_attribute("gen_ai.system", "anthropic")
        span.set_attribute("gen_ai.request.model", model)

        # === Session tracking ===
        if session_id:
            span.set_attribute("session.id", session_id)

        # === Metadata (JSON string) ===
        metadata = {}
        if is_alpha:
            metadata["source"] = "alpha"
        if metadata:
            span.set_attribute("metadata", json.dumps(metadata))

        # === Invocation parameters ===
        span.set_attribute("llm.invocation_parameters", json.dumps({
            "max_tokens": request_body.get("max_tokens", 0),
            "temperature": request_body.get("temperature", 1.0),
            "stream": request_body.get("stream", False),
        }))

        # === Input messages ===
        _add_input_messages(span, request_body)

        # === Output (if we have response) ===
        if response_body:
            _add_output(span, response_body)

        # === Status ===
        if status_code >= 400:
            span.set_status(trace.Status(trace.StatusCode.ERROR, f"HTTP {status_code}"))
        else:
            span.set_status(trace.Status(trace.StatusCode.OK))

        span.set_attribute("http.status_code", status_code)

    finally:
        span.end(end_time=end_time_ns)

    logger.info(f"LLM span created: {model}, session={session_id or 'none'}, alpha={is_alpha}")


def _add_input_messages(span: trace.Span, request_body: dict[str, Any]) -> None:
    """Add input messages as flattened OpenInference attributes."""
    idx = 0
    last_real_user_content = ""

    # System prompt first
    system = request_body.get("system")
    if system:
        span.set_attribute(f"llm.input_messages.{idx}.message.role", "system")
        span.set_attribute(f"llm.input_messages.{idx}.message.content", _extract_text(system))
        idx += 1

    # Conversation messages
    for msg in request_body.get("messages", []):
        role = msg.get("role", "unknown")
        content = _extract_text(msg.get("content", ""))

        span.set_attribute(f"llm.input_messages.{idx}.message.role", role)
        span.set_attribute(f"llm.input_messages.{idx}.message.content", content)

        # Track last user message for input.value, but skip system-reminders
        if role == "user":
            # Extract just the text blocks, not tool results
            user_text = _extract_user_text_only(msg.get("content", ""))
            # Skip hook-injected content (system-reminders, canary blocks)
            if user_text and not user_text.startswith("<system-reminder>") and "EAVESDROP_METADATA_BLOCK" not in user_text:
                last_real_user_content = user_text

            tool_results = _extract_tool_results(msg.get("content", []))
            for tool_idx, (tool_id, result) in enumerate(tool_results):
                prefix = f"llm.input_messages.{idx}.message.tool_results.{tool_idx}"
                span.set_attribute(f"{prefix}.tool_use_id", tool_id)
                span.set_attribute(f"{prefix}.content", result[:1000])  # Truncate

        idx += 1

    # Phoenix UI needs input.value for the Input column
    if last_real_user_content:
        # Truncate for display (full content is in llm.input_messages)
        span.set_attribute("input.value", last_real_user_content[:2000])
        span.set_attribute("input.mime_type", "text/plain")


def _add_output(span: trace.Span, response_body: dict[str, Any]) -> None:
    """Add output messages and token counts."""
    # Token counts
    usage = response_body.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    span.set_attribute("llm.token_count.prompt", input_tokens)
    span.set_attribute("llm.token_count.completion", output_tokens)
    span.set_attribute("llm.token_count.total", input_tokens + output_tokens)

    # Also set gen_ai.* for compatibility
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)

    # Output message
    content_blocks = response_body.get("content", [])
    output_text = _extract_text(content_blocks)
    span.set_attribute("llm.output_messages.0.message.role", "assistant")
    span.set_attribute("llm.output_messages.0.message.content", output_text)

    # Phoenix UI needs output.value for the Output column
    if output_text:
        # Truncate for display (full content is in llm.output_messages)
        span.set_attribute("output.value", output_text[:2000])
        span.set_attribute("output.mime_type", "text/plain")

    # Tool calls
    tool_idx = 0
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            prefix = f"llm.output_messages.0.message.tool_calls.{tool_idx}.tool_call"
            span.set_attribute(f"{prefix}.id", block.get("id", ""))
            span.set_attribute(f"{prefix}.function.name", block.get("name", ""))
            span.set_attribute(f"{prefix}.function.arguments", json.dumps(block.get("input", {})))
            tool_idx += 1


def _extract_text(content: Any) -> str:
    """Extract text from various Anthropic content formats."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    parts.append(f"[tool: {block.get('name', '')}]")
                elif block_type == "tool_result":
                    parts.append(f"[result: {block.get('tool_use_id', '')}]")
        return "\n".join(parts)

    return str(content) if content else ""


def _extract_user_text_only(content: Any) -> str:
    """Extract only text blocks from user message content, ignoring tool results.

    This is used to find what the user actually typed, not bundled tool results.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    parts.append(block.get("text", ""))
                # Deliberately skip tool_use and tool_result
        return "\n".join(parts)

    return ""


def _extract_tool_results(content: Any) -> list[tuple[str, str]]:
    """Extract tool results from content blocks."""
    results = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id", "")
                result_content = _extract_text(block.get("content", ""))
                results.append((tool_id, result_content))
    return results
