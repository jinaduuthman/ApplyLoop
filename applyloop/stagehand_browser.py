"""Stagehand-powered browser automation — AI-driven page interactions.

Replaces brittle CSS selectors with natural language instructions.
Uses the Stagehand SDK for act/extract/observe operations.

Enabled by setting STAGEHAND_ENABLED=true in .env.
Falls back to raw Playwright when disabled or unavailable.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

from rich.console import Console

console = Console()

_stagehand_available = False
try:
    from stagehand import Stagehand
    _stagehand_available = True
except ImportError:
    pass


def is_stagehand_enabled() -> bool:
    """Check if Stagehand is enabled and available."""
    if not _stagehand_available:
        return False
    return os.environ.get("STAGEHAND_ENABLED", "").lower() in ("1", "true", "yes")


def _resolve_model_config() -> tuple[str, str]:
    """Resolve the model name and API key for Stagehand.

    Uses STAGEHAND_MODEL and MODEL_API_KEY, falling back to
    the existing OPENAI_API_KEY / ANTHROPIC_API_KEY / OLLAMA_MODEL if set.

    Returns (model_name, api_key) in Stagehand's "provider/model" format.
    Ollama models require no API key — returns ("ollama/<model>", "ollama").
    """
    model = os.environ.get("STAGEHAND_MODEL", "")
    api_key = os.environ.get("MODEL_API_KEY", "")

    if model and api_key:
        return model, api_key

    # Ollama: no API key needed — Stagehand treats "ollama" as the sentinel
    if model and model.startswith("ollama/"):
        return model, "ollama"

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    ollama_model = os.environ.get("OLLAMA_MODEL", "")
    provider = os.environ.get("APPLYLOOP_PROVIDER", "").lower()

    if provider == "ollama" or (ollama_model and not anthropic_key and not openai_key):
        resolved = ollama_model or "llama3"
        return f"ollama/{resolved}", "ollama"

    if anthropic_key:
        return model or "anthropic/claude-sonnet-4-6", anthropic_key
    if openai_key:
        return model or "openai/gpt-4o", openai_key

    if ollama_model:
        return f"ollama/{ollama_model}", "ollama"

    return model or "openai/gpt-4o", api_key


class StagehandBrowser:
    """Wraps a Stagehand client + session for AI-powered browser automation.

    Usage:
        with StagehandBrowser() as browser:
            browser.navigate("https://example.com")
            data = browser.extract("get all job listings", schema={...})
            browser.act("click the Apply button")
    """

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._client: Any = None
        self._session: Any = None
        self._session_id: str = ""

    def __enter__(self) -> "StagehandBrowser":
        model_name, api_key = _resolve_model_config()

        if not api_key:
            raise RuntimeError(
                "Stagehand requires an LLM provider. Set MODEL_API_KEY, "
                "OPENAI_API_KEY, ANTHROPIC_API_KEY, or OLLAMA_MODEL in your .env"
            )

        server = os.environ.get("STAGEHAND_SERVER", "local")

        client_kwargs: dict[str, Any] = {
            "server": server,
            "model_api_key": api_key,
        }

        if server == "local":
            client_kwargs["browserbase_api_key"] = "local"
            client_kwargs["browserbase_project_id"] = "local"
        else:
            client_kwargs["browserbase_api_key"] = os.environ.get("BROWSERBASE_API_KEY", "")
            client_kwargs["browserbase_project_id"] = os.environ.get("BROWSERBASE_PROJECT_ID", "")

        self._client = Stagehand(**client_kwargs)
        self._client.__enter__()

        browser_config: dict[str, Any] = {
            "type": server if server == "browserbase" else "local",
        }

        session = self._client.sessions.start(
            model_name=model_name,
            browser=browser_config,
        )
        self._session = session
        self._session_id = session.id if hasattr(session, "id") else str(session.data.id if hasattr(session, "data") else "")

        console.print(f"[dim]Stagehand session started (model: {model_name}, server: {server})[/dim]")
        return self

    def __exit__(self, *exc):
        try:
            if self._session_id and self._client:
                self._client.sessions.end(self._session_id)
        except Exception:
            pass
        try:
            if self._client:
                self._client.__exit__(*exc)
        except Exception:
            pass

    def navigate(self, url: str, wait_ms: int = 3000) -> None:
        """Navigate to a URL."""
        self._client.sessions.navigate(self._session_id, url=url)

    def extract(
        self,
        instruction: str,
        schema: dict[str, Any],
    ) -> Any:
        """Extract structured data from the current page using AI.

        Args:
            instruction: Natural language description of what to extract.
            schema: JSON Schema for the expected output structure.

        Returns:
            Parsed data matching the schema.
        """
        response = self._client.sessions.extract(
            self._session_id,
            instruction=instruction,
            schema=schema,
        )
        return response.data if hasattr(response, "data") else response

    def act(self, instruction: str, variables: dict[str, str] | None = None) -> Any:
        """Perform a browser action using natural language.

        Args:
            instruction: What to do, e.g. "click the Apply button".
                Use %var% syntax for variable substitution.
            variables: Optional variable substitutions for the instruction.

        Returns:
            Action response from Stagehand.
        """
        kwargs: dict[str, Any] = {"input": instruction}
        if variables:
            kwargs["variables"] = variables

        response = self._client.sessions.act(self._session_id, **kwargs)
        return response.data if hasattr(response, "data") else response

    def observe(self, instruction: str) -> list[dict[str, Any]]:
        """Discover actionable elements on the current page.

        Args:
            instruction: What to look for, e.g. "find all form fields".

        Returns:
            List of action objects with selectors and descriptions.
        """
        response = self._client.sessions.observe(
            self._session_id,
            instruction=instruction,
        )
        if hasattr(response, "data"):
            return response.data if isinstance(response.data, list) else [response.data]
        return response if isinstance(response, list) else [response]


# ── High-level extraction schemas ─────────────────────────────────

JOB_LISTING_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Job title"},
                    "company": {"type": "string", "description": "Company name"},
                    "location": {"type": "string", "description": "Job location"},
                    "url": {"type": "string", "description": "URL/link to the job posting"},
                    "salary": {"type": "string", "description": "Salary or pay range if shown"},
                    "posted_date": {"type": "string", "description": "When the job was posted"},
                },
                "required": ["title"],
            },
        }
    },
    "required": ["jobs"],
}

JOB_DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Job title"},
        "company": {"type": "string", "description": "Company name"},
        "location": {"type": "string", "description": "Job location (city, state, remote, etc.)"},
        "description": {"type": "string", "description": "Full job description text"},
        "requirements": {"type": "string", "description": "Requirements and qualifications section"},
        "is_expired": {"type": "boolean", "description": "Whether the posting appears closed or expired"},
    },
    "required": ["title", "description"],
}

FORM_FIELDS_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "The field label text"},
                    "type": {"type": "string", "description": "Field type: text, email, tel, file, textarea, select, checkbox, radio"},
                    "required": {"type": "boolean", "description": "Whether the field is required"},
                    "name": {"type": "string", "description": "HTML name attribute if visible"},
                },
                "required": ["label", "type"],
            },
        },
        "has_resume_upload": {"type": "boolean"},
        "has_cover_letter_field": {"type": "boolean"},
        "submit_button_text": {"type": "string"},
    },
    "required": ["fields"],
}
