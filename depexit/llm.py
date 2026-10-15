"""
LLM Client for GemmaMorph.

Supports both hosted Groq (Gemma2-9B-IT) and local Ollama (Gemma2-2B),
with automatic backend selection, rich console logging, defensive JSON extraction,
and self-repair capabilities.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import find_dotenv, load_dotenv
from rich.console import Console

# Load environment variables at module import time
_current_dir = Path(__file__).resolve().parent
_repo_env = _current_dir.parent / ".env"
if _repo_env.exists():
    load_dotenv(dotenv_path=_repo_env, override=False)
else:
    load_dotenv(find_dotenv(), override=False)

console = Console()

DEFAULT_GROQ_MODEL = os.getenv("GEMMA_MODEL", "gemma2-9b-it")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2:2b")
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/api/generate"


def _format_preview(text: str, max_chars: int = 80) -> str:
    """Format single-line preview string for console logging."""
    flattened = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if len(flattened) > max_chars:
        return flattened[:max_chars] + "..."
    return flattened


def _call_groq(prompt: str, system: str = "") -> str:
    """Execute LLM call using hosted Groq API with Gemma model."""
    try:
        from groq import Groq
    except ImportError as exc:
        raise ImportError(
            "The 'groq' package is required for hosted Groq inference. Install with 'pip install groq'."
        ) from exc

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key == "your_groq_api_key_here":
        raise ValueError("GROQ_API_KEY is missing or contains placeholder in environment/.env file.")

    model = os.getenv("GEMMA_MODEL", DEFAULT_GROQ_MODEL)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.1,
    )

    content = response.choices[0].message.content or ""
    return content


def _call_ollama(prompt: str, system: str = "") -> str:
    """Execute LLM call using local Ollama service."""
    model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    url = DEFAULT_OLLAMA_URL

    full_prompt = f"System: {system}\n\nUser: {prompt}" if system else prompt
    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
    }

    response = requests.post(url, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data.get("response", "")


def _call_mock(prompt: str, system: str = "") -> str:
    """Mock offline LLM provider simulating Gemma responses for LinkWatch dependencies."""
    p_lower = prompt.lower()
    if "target dependency: appdirs" in p_lower:
        return json.dumps({
            "action": "replace",
            "replacement_package": "platformdirs",
            "replacement_version": ">=4.0.0",
            "reasoning": "appdirs has been unmaintained since 2017. platformdirs is its drop-in successor with the same API.",
            "migration_steps": [
                "Replace appdirs with platformdirs in requirements.txt",
                "Update imports in linkwatch/cache.py from appdirs to platformdirs"
            ],
            "code_edits": [
                {
                    "file": "linkwatch/cache.py",
                    "old_text": "from appdirs import user_cache_dir",
                    "new_text": "from platformdirs import user_cache_dir"
                }
            ],
            "requirements_edits": {
                "remove": ["appdirs==1.4.4"],
                "add": ["platformdirs>=4.0.0"]
            },
            "expected_gaps": [
                "platformdirs uses updated standard directory specs on Linux; Windows behavior is identical."
            ],
            "suggested_tests": [
                "Run tests/test_cache.py to verify cache directory creation"
            ]
        }, indent=2)

    if "target dependency: toml" in p_lower:
        return json.dumps({
            "action": "replace",
            "replacement_package": "tomllib",
            "replacement_version": "stdlib",
            "reasoning": "toml is deprecated. Python 3.11+ includes the PEP 680 tomllib module in the standard library.",
            "migration_steps": [
                "Replace import toml with import tomllib in linkwatch/config.py",
                "Update load_settings to read in binary mode open(path, 'rb')",
                "Remove toml from requirements.txt"
            ],
            "code_edits": [
                {
                    "file": "linkwatch/config.py",
                    "old_text": "import toml",
                    "new_text": "import tomllib"
                },
                {
                    "file": "linkwatch/config.py",
                    "old_text": "def load_settings(path=\"sites.toml\"):\n    data = toml.load(path)\n    return Settings(**data)",
                    "new_text": "def load_settings(path=\"sites.toml\"):\n    with open(path, \"rb\") as fh:\n        data = tomllib.load(fh)\n    return Settings(**data)"
                }
            ],
            "requirements_edits": {
                "remove": ["toml==0.10.2"],
                "add": []
            },
            "expected_gaps": [
                "tomllib is read-only. It does not provide serialization/dumping, which LinkWatch does not need."
            ],
            "suggested_tests": [
                "Run tests/test_config.py to verify sites.toml loading"
            ]
        }, indent=2)

    if "target dependency: six" in p_lower:
        return json.dumps({
            "action": "remove",
            "replacement_package": None,
            "replacement_version": None,
            "reasoning": "six is a Python 2 compatibility layer. Python 3.12 target has 0 AST usages in the codebase.",
            "migration_steps": [
                "Remove six from requirements.txt"
            ],
            "code_edits": [],
            "requirements_edits": {
                "remove": ["six"],
                "add": []
            },
            "expected_gaps": [],
            "suggested_tests": [
                "Run full test suite to confirm no transitive dependencies fail"
            ]
        }, indent=2)

    if "target dependency: pydantic" in p_lower:
        return json.dumps({
            "action": "upgrade",
            "replacement_package": "pydantic",
            "replacement_version": ">=2.7.0",
            "reasoning": "Pydantic 1.x is legacy. Migrate to Pydantic 2.x using @field_validator with mode='before'.",
            "migration_steps": [
                "Upgrade pydantic to >=2.7.0 in requirements.txt",
                "Change validator to field_validator in linkwatch/config.py",
                "Update validator decorators to @field_validator(..., mode='before')"
            ],
            "code_edits": [
                {
                    "file": "linkwatch/config.py",
                    "old_text": "from pydantic import BaseModel, validator",
                    "new_text": "from pydantic import BaseModel, field_validator"
                },
                {
                    "file": "linkwatch/config.py",
                    "old_text": "    @validator(\"name\")",
                    "new_text": "    @field_validator(\"name\", mode=\"before\")"
                },
                {
                    "file": "linkwatch/config.py",
                    "old_text": "    @validator(\"url\")",
                    "new_text": "    @field_validator(\"url\", mode=\"before\")"
                }
            ],
            "requirements_edits": {
                "remove": ["pydantic==1.10.13"],
                "add": ["pydantic>=2.7.0"]
            },
            "expected_gaps": [
                "Pydantic v2 has updated config model attributes."
            ],
            "suggested_tests": [
                "Run tests/test_config.py to verify validation logic"
            ]
        }, indent=2)

    if "target dependency: jinja2" in p_lower:
        return json.dumps({
            "action": "upgrade",
            "replacement_package": "Jinja2",
            "replacement_version": ">=3.1.4",
            "reasoning": "Jinja2 2.11.3 breaks on modern MarkupSafe>=2.1 due to soft_unicode removal. Upgrading restores compatibility.",
            "migration_steps": [
                "Update Jinja2 to >=3.1.4 in requirements.txt"
            ],
            "code_edits": [],
            "requirements_edits": {
                "remove": ["Jinja2==2.11.3"],
                "add": ["Jinja2>=3.1.4"]
            },
            "expected_gaps": [],
            "suggested_tests": [
                "Run tests/test_report.py to verify report generation"
            ]
        }, indent=2)

    return json.dumps({
        "action": "upgrade",
        "replacement_package": None,
        "replacement_version": None,
        "reasoning": "Package flagged as deprecated. Recommend updating to the latest secure release.",
        "migration_steps": ["Inspect dependency usages", "Update requirements.txt"],
        "code_edits": [],
        "requirements_edits": {"remove": [], "add": []},
        "expected_gaps": [],
        "suggested_tests": ["Run test suite"]
    }, indent=2)


def ask_llm(prompt: str, system: str = "") -> str:
    """
    Unified LLM call supporting Groq and Ollama backends.

    Backend selection:
    1. If `LLM_BACKEND` env var is 'groq' or 'ollama', use that.
    2. Default: try groq first (if GROQ_API_KEY is configured), else try ollama.

    Args:
        prompt: User prompt content.
        system: Optional system instructions.

    Returns:
        Raw string response from the LLM.
    """
    selected_backend = os.getenv("LLM_BACKEND", "").strip().lower()
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    has_valid_groq_key = bool(api_key and api_key != "your_groq_api_key_here")

    # Log query preview
    prompt_prev = _format_preview(prompt, 80)
    active_name = selected_backend or ("groq" if has_valid_groq_key else "ollama")
    console.log(f"[bold cyan]LLM Query[/bold cyan] [dim]({active_name})[/dim]: [dim]{prompt_prev}[/dim]")

    result: str = ""
    last_error: Optional[Exception] = None

    if selected_backend in ("mock", "demo", "offline") or os.getenv("MOCK_LLM") == "1":
        result = _call_mock(prompt, system)
    elif selected_backend == "groq":
        result = _call_groq(prompt, system)
    elif selected_backend == "ollama":
        result = _call_ollama(prompt, system)
    else:
        # Default behavior: try Groq if key exists, else fallback to Ollama
        if has_valid_groq_key:
            try:
                result = _call_groq(prompt, system)
            except Exception as exc:
                last_error = exc
                console.log(f"[yellow]Groq failed ({exc}), falling back to local Ollama...[/yellow]")
                try:
                    result = _call_ollama(prompt, system)
                except Exception as ollama_exc:
                    raise RuntimeError(
                        f"Both Groq and Ollama LLM calls failed.\n"
                        f"Groq error: {last_error}\n"
                        f"Ollama error: {ollama_exc}"
                    ) from ollama_exc
        else:
            try:
                result = _call_ollama(prompt, system)
            except Exception as ollama_exc:
                raise RuntimeError(
                    f"No valid GROQ_API_KEY found, and local Ollama connection failed: {ollama_exc}.\n"
                    f"Please either:\n"
                    f"1. Set a valid GROQ_API_KEY in .env or environment.\n"
                    f"2. Ensure Ollama is running at {DEFAULT_OLLAMA_URL} with model {DEFAULT_OLLAMA_MODEL}."
                ) from ollama_exc

    # Log response preview
    resp_prev = _format_preview(result, 80)
    console.log(f"[bold green]LLM Response[/bold green]: [dim]{resp_prev}[/dim]")
    return result


def _clean_json_candidate(text: str) -> str:
    """Preprocess JSON text to strip markdown code blocks and trailing commas."""
    # Strip markdown fences if present
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if code_block_match:
        text = code_block_match.group(1).strip()

    # Search for first outer {...} or [...] block
    bracket_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if bracket_match:
        text = bracket_match.group(1).strip()

    # Clean trailing commas before closing braces/brackets
    text = re.sub(r",\s*([\}\]])", r"\1", text)
    return text


def parse_json_from_llm(raw: str, max_repair_attempts: int = 1) -> dict:
    """
    Defensively extract and parse JSON from LLM output.

    Handles markdown fences, leading/trailing conversational text, and minor syntax issues.
    If parsing fails, triggers one repair attempt asking the LLM to fix its own output.

    Args:
        raw: Raw text output from LLM.
        max_repair_attempts: Number of self-repair LLM queries to attempt on syntax error.

    Returns:
        Parsed dict (or list wrapped in dict if array was returned).

    Raises:
        ValueError: If JSON cannot be parsed even after repair attempts.
    """
    cleaned = _clean_json_candidate(raw)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
        return {"value": parsed}
    except Exception as initial_err:
        console.log(f"[bold yellow]JSON parse warning:[/bold yellow] {initial_err}. Input was:\n{cleaned[:150]}...")

        if max_repair_attempts > 0:
            console.log("[bold yellow]Attempting LLM self-repair on malformed JSON...[/bold yellow]")
            repair_system = (
                "You are a strict JSON repair engine. Your only job is to fix broken JSON syntax. "
                "Output ONLY the corrected, valid JSON object. No explanations, no markdown fences."
            )
            repair_prompt = (
                f"The following output was intended to be valid JSON but failed to parse.\n"
                f"Parse error: {initial_err}\n\n"
                f"Raw input:\n{raw}\n\n"
                f"Please fix all syntax errors (quotes, missing brackets, trailing commas) "
                f"and return ONLY the valid JSON object."
            )
            try:
                repaired_raw = ask_llm(repair_prompt, system=repair_system)
                return parse_json_from_llm(repaired_raw, max_repair_attempts=max_repair_attempts - 1)
            except Exception as repair_err:
                console.log(f"[bold red]LLM self-repair failed:[/bold red] {repair_err}")

        raise ValueError(f"Failed to parse valid JSON from LLM output: {initial_err}") from initial_err


if __name__ == "__main__":
    console.print("[bold blue]Testing GemmaMorph LLM module...[/bold blue]")
    test_json_string = """
    Here is the migration plan you requested:
    ```json
    {
        "action": "replace",
        "replacement_package": "platformdirs",
        "reasoning": "appdirs is deprecated and unmaintained since 2017.",
        "migration_steps": ["pip install platformdirs", "update imports"]
    }
    ```
    I hope this helps!
    """
    parsed_sample = parse_json_from_llm(test_json_string)
    console.print("[bold green]Parsed defensive JSON successfully:[/bold green]")
    console.print(parsed_sample)
