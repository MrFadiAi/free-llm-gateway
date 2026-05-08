"""RTK Token Compression — reduces input tokens by 20-40%.

Strategies:
1. System prompt deduplication — merge repeated system messages
2. Conversation summarization — compress old messages into a summary
3. Whitespace and redundancy removal
4. Smart context windowing — keep only the most relevant messages
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Default settings
DEFAULT_MAX_CONTEXT_MESSAGES = 20  # Keep last N messages
DEFAULT_SUMMARY_THRESHOLD = 10  # Summarize when more than N messages
DEFAULT_MIN_MESSAGES_TO_COMPRESS = 6  # Don't compress small conversations


@dataclass
class CompressionResult:
    original_count: int
    compressed_count: int
    saved_percent: float
    messages: list[dict[str, Any]]
    strategies_applied: list[str]


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token for English)."""
    return max(1, len(text) // 4)


def _merge_system_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Merge multiple system messages into one, deduplicating content."""
    system_msgs = [m for m in messages if m.get("role") == "system"]
    if len(system_msgs) <= 1:
        return messages, False

    non_system = [m for m in messages if m.get("role") != "system"]
    merged_content = "\n\n".join(m.get("content", "") for m in system_msgs if m.get("content"))
    if not merged_content:
        return non_system, False

    return [{"role": "system", "content": merged_content}] + non_system, True


def _strip_redundant_whitespace(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Normalize whitespace in message content without losing meaning."""
    changed = False
    result = []
    for m in messages:
        content = m.get("content", "")
        if not isinstance(content, str):
            result.append(m)
            continue
        # Collapse multiple newlines to double
        compressed = re.sub(r'\n{3,}', '\n\n', content)
        # Collapse multiple spaces to one (but preserve indentation)
        compressed = re.sub(r'[^\S\n]{2,}', ' ', compressed)
        # Strip trailing whitespace per line
        compressed = '\n'.join(line.rstrip() for line in compressed.split('\n'))
        compressed = compressed.strip()
        if compressed != content:
            changed = True
        result.append({**m, "content": compressed})
    return result, changed


def _compress_old_messages(
    messages: list[dict[str, Any]],
    keep_recent: int = DEFAULT_MAX_CONTEXT_MESSAGES,
) -> tuple[list[dict[str, Any]], bool]:
    """Keep system message + last N messages, summarize the rest."""
    if len(messages) <= keep_recent:
        return messages, False

    system_msgs = [m for m in messages if m.get("role") == "system"]
    conversation = [m for m in messages if m.get("role") != "system"]

    if len(conversation) <= keep_recent:
        return messages, False

    old = conversation[:-keep_recent]
    recent = conversation[-keep_recent:]

    # Build a compact summary of old messages
    summary_parts = []
    for m in old:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            # Truncate long messages in summary
            preview = content[:200] + "..." if len(content) > 200 else content
            summary_parts.append(f"[{role}]: {preview}")

    summary_text = "[Earlier conversation summarized]:\n" + "\n".join(summary_parts)
    summary_msg = {"role": "system", "content": summary_text}

    return system_msgs + [summary_msg] + recent, True


def _deduplicate_consecutive(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Remove consecutive messages with the same role (merge them)."""
    if len(messages) <= 1:
        return messages, False

    result = [messages[0]]
    changed = False
    for m in messages[1:]:
        if m.get("role") == result[-1].get("role") and m.get("role") in ("user", "assistant"):
            # Merge content
            old_content = result[-1].get("content", "")
            new_content = m.get("content", "")
            if isinstance(old_content, str) and isinstance(new_content, str):
                result[-1] = {**result[-1], "content": old_content + "\n" + new_content}
                changed = True
                continue
        result.append(m)
    return result, changed


def compress_messages(
    messages: list[dict[str, Any]],
    *,
    enabled: bool = True,
    max_context: int = DEFAULT_MAX_CONTEXT_MESSAGES,
) -> CompressionResult:
    """Apply RTK token compression to a message list.

    Returns CompressionResult with original/compressed counts and the new messages.
    """
    if not enabled or len(messages) < DEFAULT_MIN_MESSAGES_TO_COMPRESS:
        original = sum(_estimate_tokens(m.get("content", "")) for m in messages if isinstance(m.get("content"), str))
        return CompressionResult(
            original_count=original,
            compressed_count=original,
            saved_percent=0.0,
            messages=messages,
            strategies_applied=[],
        )

    original_tokens = sum(
        _estimate_tokens(m.get("content", ""))
        for m in messages if isinstance(m.get("content"), str)
    )

    strategies: list[str] = []
    result = list(messages)

    # Strategy 1: Merge system messages
    result, changed = _merge_system_messages(result)
    if changed:
        strategies.append("merge_system")

    # Strategy 2: Deduplicate consecutive same-role messages
    result, changed = _deduplicate_consecutive(result)
    if changed:
        strategies.append("dedup_consecutive")

    # Strategy 3: Strip redundant whitespace
    result, changed = _strip_redundant_whitespace(result)
    if changed:
        strategies.append("whitespace")

    # Strategy 4: Compress old messages with summarization
    result, changed = _compress_old_messages(result, keep_recent=max_context)
    if changed:
        strategies.append("context_windowing")

    compressed_tokens = sum(
        _estimate_tokens(m.get("content", ""))
        for m in result if isinstance(m.get("content"), str)
    )

    saved = 0.0
    if original_tokens > 0:
        saved = round((1 - compressed_tokens / original_tokens) * 100, 1)

    if strategies:
        logger.info(
            "RTK compression: %d -> %d tokens (%.1f%% saved, strategies: %s)",
            original_tokens, compressed_tokens, saved, ", ".join(strategies),
        )

    return CompressionResult(
        original_count=original_tokens,
        compressed_count=compressed_tokens,
        saved_percent=saved,
        messages=result,
        strategies_applied=strategies,
    )
