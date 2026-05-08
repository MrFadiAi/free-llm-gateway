"""Format Translation — translate between OpenAI, Claude, Gemini, and Vertex API formats.

Supports bidirectional conversion so the gateway can accept requests in any format
and route them to any provider, regardless of the target provider's native format.

Formats:
- openai: Standard OpenAI chat completions format (default gateway format)
- claude: Anthropic Claude Messages API format
- gemini: Google Gemini generateContent format
- vertex: Google Vertex AI format (similar to Gemini with auth differences)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

FormatType = str  # "openai" | "claude" | "gemini" | "vertex"


def detect_format(body: dict[str, Any]) -> FormatType:
    """Auto-detect the request format from the body structure."""
    # Claude format: has "messages" but also anthropic-specific fields
    if "system" in body and isinstance(body.get("system"), (str, list)):
        if "max_tokens" in body:
            return "claude"

    # Gemini format: has "contents" instead of "messages"
    if "contents" in body:
        return "gemini"

    # Vertex format: similar to Gemini but with specific structure
    if "instances" in body:
        return "vertex"

    # Default: OpenAI format (has "messages" + "model")
    return "openai"


def translate_to_openai(body: dict[str, Any], source_format: FormatType) -> dict[str, Any]:
    """Convert any format to OpenAI chat completions format."""
    if source_format == "openai":
        return body

    if source_format == "claude":
        return _claude_to_openai(body)
    if source_format == "gemini":
        return _gemini_to_openai(body)
    if source_format == "vertex":
        return _vertex_to_openai(body)

    return body


def translate_from_openai(body: dict[str, Any], target_format: FormatType) -> dict[str, Any]:
    """Convert OpenAI format to the target format."""
    if target_format == "openai":
        return body

    if target_format == "claude":
        return _openai_to_claude(body)
    if target_format == "gemini":
        return _openai_to_gemini(body)
    if target_format == "vertex":
        return _openai_to_vertex(body)

    return body


# ── Claude ↔ OpenAI ──────────────────────────────────────────────────────────

def _claude_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Convert Anthropic Claude Messages format to OpenAI format."""
    messages: list[dict[str, Any]] = []

    # Claude system prompt handling
    system = body.get("system")
    if system:
        sys_content = system if isinstance(system, str) else _extract_claude_content(system)
        if sys_content:
            messages.append({"role": "system", "content": sys_content})

    # Convert Claude messages to OpenAI format
    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Claude content can be a string or array of content blocks
        if isinstance(content, list):
            text_parts = []
            tool_use_blocks = []
            tool_results = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_use_blocks.append(block)
                    elif block.get("type") == "tool_result":
                        tool_results.append(block)

            if text_parts:
                messages.append({"role": role, "content": "\n".join(text_parts)})

            # Convert tool_use to OpenAI tool_calls format
            if tool_use_blocks and role == "assistant":
                tool_calls = []
                for i, block in enumerate(tool_use_blocks):
                    tool_calls.append({
                        "id": block.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": _to_json_str(block.get("input", {})),
                        },
                    })
                messages.append({
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                    "tool_calls": tool_calls,
                })

            # Convert tool_result to OpenAI tool message
            if tool_results:
                for block in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": _extract_claude_content(block.get("content", "")),
                    })
        else:
            messages.append({"role": role, "content": str(content)})

    result: dict[str, Any] = {
        "model": body.get("model", ""),
        "messages": messages,
    }

    # Map parameters
    if "max_tokens" in body:
        result["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        result["temperature"] = body["temperature"]
    if "top_p" in body:
        result["top_p"] = body["top_p"]
    if "stop_sequences" in body:
        result["stop"] = body["stop_sequences"]
    if body.get("stream"):
        result["stream"] = True

    # Convert Claude tools to OpenAI tools
    if "tools" in body:
        openai_tools = []
        for tool in body["tools"]:
            if tool.get("type") == "custom" or "name" in tool:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {}),
                    },
                })
        if openai_tools:
            result["tools"] = openai_tools

    return result


def _openai_to_claude(body: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAI format to Anthropic Claude Messages format."""
    messages: list[dict[str, Any]] = []
    system_content = ""

    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_content += (content + "\n") if isinstance(content, str) else ""
            continue

        # OpenAI tool calls -> Claude tool_use blocks
        if role == "assistant" and "tool_calls" in msg:
            content_blocks = []
            if content:
                content_blocks.append({"type": "text", "text": content})
            for tc in msg["tool_calls"]:
                if tc.get("type") == "function":
                    func = tc.get("function", {})
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": _from_json_str(func.get("arguments", "{}")),
                    })
            messages.append({"role": "assistant", "content": content_blocks})
            continue

        # OpenAI tool response -> Claude tool_result
        if role == "tool":
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": str(content) if content else "",
                }],
            })
            continue

        messages.append({"role": role, "content": str(content) if content else ""})

    result: dict[str, Any] = {
        "model": body.get("model", ""),
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
    }

    if system_content.strip():
        result["system"] = system_content.strip()
    if "temperature" in body:
        result["temperature"] = body["temperature"]
    if "top_p" in body:
        result["top_p"] = body["top_p"]
    if "stop" in body:
        result["stop_sequences"] = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
    if body.get("stream"):
        result["stream"] = True

    # Convert OpenAI tools to Claude tools
    if "tools" in body:
        claude_tools = []
        for tool in body["tools"]:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                claude_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                })
        if claude_tools:
            result["tools"] = claude_tools

    return result


# ── Gemini ↔ OpenAI ──────────────────────────────────────────────────────────

def _gemini_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Convert Google Gemini format to OpenAI format."""
    messages: list[dict[str, Any]] = []

    # System instruction
    sys_instr = body.get("systemInstruction")
    if sys_instr:
        sys_text = _extract_gemini_parts(sys_instr)
        if sys_text:
            messages.append({"role": "system", "content": sys_text})

    # Convert contents
    for content in body.get("contents", []):
        role = "assistant" if content.get("role") == "model" else "user"
        parts_text = _extract_gemini_parts(content)
        if parts_text:
            messages.append({"role": role, "content": parts_text})

    result: dict[str, Any] = {
        "model": body.get("model", ""),
        "messages": messages,
    }

    # Map generation config
    gen_config = body.get("generationConfig", {})
    if "maxOutputTokens" in gen_config:
        result["max_tokens"] = gen_config["maxOutputTokens"]
    if "temperature" in gen_config:
        result["temperature"] = gen_config["temperature"]
    if "topP" in gen_config:
        result["top_p"] = gen_config["topP"]
    if "stopSequences" in gen_config:
        result["stop"] = gen_config["stopSequences"]

    return result


def _openai_to_gemini(body: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAI format to Google Gemini format."""
    contents: list[dict[str, Any]] = []
    system_instruction = None

    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_instruction = {"parts": [{"text": str(content)}]}
            continue

        gemini_role = "model" if role == "assistant" else "user"
        contents.append({
            "role": gemini_role,
            "parts": [{"text": str(content)}],
        })

    result: dict[str, Any] = {
        "model": body.get("model", ""),
        "contents": contents,
    }

    if system_instruction:
        result["systemInstruction"] = system_instruction

    # Map to generationConfig
    gen_config: dict[str, Any] = {}
    if "max_tokens" in body:
        gen_config["maxOutputTokens"] = body["max_tokens"]
    if "temperature" in body:
        gen_config["temperature"] = body["temperature"]
    if "top_p" in body:
        gen_config["topP"] = body["top_p"]
    if "stop" in body:
        gen_config["stopSequences"] = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
    if gen_config:
        result["generationConfig"] = gen_config

    return result


# ── Vertex ↔ OpenAI ──────────────────────────────────────────────────────────

def _vertex_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Convert Vertex AI format to OpenAI format."""
    instances = body.get("instances", [])
    if not instances:
        return {"model": body.get("model", ""), "messages": []}

    first = instances[0]
    # Vertex instances often follow the Gemini-like structure
    return _gemini_to_openai(first if isinstance(first, dict) else {"contents": []})


def _openai_to_vertex(body: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAI format to Vertex AI format."""
    gemini_body = _openai_to_gemini(body)
    return {
        "model": body.get("model", ""),
        "instances": [gemini_body],
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_claude_content(content: str | list) -> str:
    """Extract text from Claude content (string or content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _extract_gemini_parts(content: dict[str, Any]) -> str:
    """Extract text from Gemini parts."""
    parts = content.get("parts", [])
    texts = []
    for part in parts:
        if isinstance(part, dict) and "text" in part:
            texts.append(part["text"])
    return "\n".join(texts)


def _to_json_str(obj: Any) -> str:
    """Safely convert to JSON string."""
    if isinstance(obj, str):
        return obj
    import json
    try:
        return json.dumps(obj)
    except (TypeError, ValueError):
        return "{}"


def _from_json_str(s: str) -> Any:
    """Safely parse JSON string."""
    import json
    if not isinstance(s, str):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return {}


def translate_request(body: dict[str, Any], target_format: FormatType = "openai") -> dict[str, Any]:
    """Auto-detect source format and translate to target format."""
    source = detect_format(body)
    if source == target_format:
        return body

    logger.info("Format translation: %s -> %s", source, target_format)

    # First convert to OpenAI (canonical), then to target
    openai_body = translate_to_openai(body, source)
    return translate_from_openai(openai_body, target_format)


def translate_response(
    response: dict[str, Any],
    target_format: FormatType,
) -> dict[str, Any]:
    """Translate an OpenAI-format response to the target format."""
    if target_format == "openai":
        return response

    if target_format == "claude":
        return _openai_response_to_claude(response)
    if target_format == "gemini":
        return _openai_response_to_gemini(response)

    return response


def _openai_response_to_claude(response: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAI response to Claude response format."""
    choices = response.get("choices", [])
    if not choices:
        return response

    choice = choices[0]
    message = choice.get("message", {})

    content_blocks = []
    text = message.get("content")
    if text:
        content_blocks.append({"type": "text", "text": text})

    # Tool calls
    for tc in message.get("tool_calls", []):
        if tc.get("type") == "function":
            func = tc.get("function", {})
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "input": _from_json_str(func.get("arguments", "{}")),
            })

    usage = response.get("usage", {})
    return {
        "id": response.get("id", ""),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": response.get("model", ""),
        "stop_reason": _map_stop_reason(choice.get("finish_reason", "")),
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _openai_response_to_gemini(response: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAI response to Gemini response format."""
    choices = response.get("choices", [])
    if not choices:
        return response

    choice = choices[0]
    message = choice.get("message", {})

    parts = []
    text = message.get("content")
    if text:
        parts.append({"text": text})

    usage = response.get("usage", {})
    return {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": parts,
            },
            "finishReason": _map_stop_reason_gemini(choice.get("finish_reason", "")),
        }],
        "usageMetadata": {
            "promptTokenCount": usage.get("prompt_tokens", 0),
            "candidatesTokenCount": usage.get("completion_tokens", 0),
            "totalTokenCount": usage.get("total_tokens", 0),
        },
        "modelVersion": response.get("model", ""),
    }


def _map_stop_reason(finish_reason: str) -> str:
    """Map OpenAI finish_reason to Claude stop_reason."""
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }
    return mapping.get(finish_reason, "end_turn")


def _map_stop_reason_gemini(finish_reason: str) -> str:
    """Map OpenAI finish_reason to Gemini finishReason."""
    mapping = {
        "stop": "STOP",
        "length": "MAX_TOKENS",
        "tool_calls": "STOP",
        "content_filter": "SAFETY",
    }
    return mapping.get(finish_reason, "STOP")
