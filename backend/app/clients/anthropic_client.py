"""Anthropic Messages API client, for the AI deck review.

The review reply is forced through a tool schema (``emit_review``) so the answer
arrives as structured JSON to validate, never prose to parse (ARCHITECTURE.md
section 2.4). The client is deliberately thin: budgets, caching, validation and
post-filtering all live in ``services/rating/ai_review.py``, where they can be
tested with this class mocked out.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.clients.base import ExternalClient, SourceResponseError
from app.config import Settings

API_VERSION = "2023-06-01"


class AnthropicClient(ExternalClient):
    """Messages API access with a forced tool response."""

    service: ClassVar[str] = "anthropic"
    base_url: ClassVar[str] = "https://api.anthropic.com"
    timeout_s: ClassVar[float] = 90.0
    max_attempts: ClassVar[int] = 3
    respect_robots: ClassVar[bool] = False

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        super().__init__("MTGVault/1.0 (self-hosted deck review)", **kwargs)
        self._api_key = settings.anthropic_api_key or ""
        self.model = settings.anthropic_model

    async def forced_tool_call(
        self,
        *,
        system: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 2048,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """One Messages API call whose only allowed reply is the given tool.

        Returns:
            The tool input the model emitted, and ``{"input_tokens", "output_tokens"}``.

        Raises:
            SourceResponseError: The reply carried no matching tool call.
        """
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            "tools": [
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": input_schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        payload = await self.request_json(
            "/v1/messages",
            method="POST",
            json=body,
            headers={"x-api-key": self._api_key, "anthropic-version": API_VERSION},
        )
        for block in payload.get("content") or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == tool_name
                and isinstance(block.get("input"), dict)
            ):
                usage = payload.get("usage") or {}
                return block["input"], {
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                }
        raise SourceResponseError(
            "The model reply carried no tool call",
            detail={"service": self.service, "stop_reason": payload.get("stop_reason")},
        )
