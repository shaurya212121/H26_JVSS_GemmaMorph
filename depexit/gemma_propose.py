"""
Stage 4: Gemma Proposal Generator for GemmaMorph.

Generates precise migration proposals for at-risk dependencies using Google's Gemma model.
Constructs rich AST-grounded prompts, extracts JSON defensively via llm.py, validates
every code edit against target source files, and produces certified migration plans.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Ensure project root is on sys.path for direct script execution
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from rich import box
from rich.console import Console
from rich.table import Table

try:
    from depexit.llm import ask_llm, parse_json_from_llm
except ImportError:
    from llm import ask_llm, parse_json_from_llm

console = Console()

GEMMA_SYSTEM_PROMPT = (
    "You are an expert Python dependency migration specialist. "
    "You analyze deprecated Python packages and generate precise, minimal code edits "
    "to migrate to modern replacements. Always respond with valid JSON only. "
    "Never use unified diff format - use exact old_text/new_text pairs instead. "
    "Be conservative: if unsure about an edit, omit it and note it in expected_gaps."
)

# Known ground-truth migration patterns for LinkWatch reference
KNOWN_MIGRATIONS_GUIDE = """
Ground truth migration rules for common deprecated packages:
1. 'appdirs':
   - Action: 'replace'
   - Replacement: 'platformdirs' (version: '>=4.0.0')
   - Drop-in replacement with identical API: `from platformdirs import user_cache_dir`
   - Requirements: remove 'appdirs==1.4.4', add 'platformdirs>=4.0.0'
2. 'toml':
   - Action: 'replace'
   - Replacement: 'tomllib' (built-in standard library in Python 3.11+)
   - Note: 'tomllib.load' requires binary file object `open(path, 'rb')`. E.g.:
     `with open(path, "rb") as fh: data = tomllib.load(fh)`
   - Requirements: remove 'toml==0.10.2', add nothing (stdlib)
3. 'pydantic':
   - Action: 'upgrade'
   - Replacement: 'pydantic' (version: '>=2.7.0')
   - Migrate `from pydantic import BaseModel, validator` to `from pydantic import BaseModel, field_validator`
   - Migrate `@validator(...)` to `@field_validator(..., mode='before')`
   - Requirements: remove 'pydantic==1.10.13', add 'pydantic>=2.7.0'
4. 'Jinja2':
   - Action: 'upgrade'
   - Replacement: 'Jinja2' (version: '>=3.1.4')
   - Fixes compatibility with modern MarkupSafe (e.g. soft_unicode removal)
   - Requirements: remove 'Jinja2==2.11.3', add 'Jinja2>=3.1.4'
5. 'six':
   - Action: 'remove'
   - Obsolete Python 2 compatibility shim. If 0 usages in code, remove entirely.
   - Requirements: remove 'six', add nothing
6. 'setuptools':
   - Action: 'upgrade'
   - Note: 'pkg_resources' API is deprecated in setuptools; recommend modern importlib.resources.
"""


def resolve_target_repo(repo_path: Optional[Path] = None) -> Path:
    """Resolve target repository path from argument, environment, or default relative location."""
    if repo_path:
        return Path(repo_path).resolve()

    env_target = os.getenv("TARGET_REPO")
    if env_target:
        candidate = (Path(__file__).resolve().parent.parent / env_target).resolve()
        if candidate.exists():
            return candidate
        direct = Path(env_target).resolve()
        if direct.exists():
            return direct

    # Default to ../LinkWatch relative to GemmaMorph repository root
    default_path = (Path(__file__).resolve().parent.parent.parent / "LinkWatch").resolve()
    return default_path


def _format_usages_for_prompt(usages_list: list[dict[str, Any]]) -> str:
    """Format AST usage findings into clear, structured context for the LLM prompt."""
    if not usages_list:
        return "No source code usages found (dependency is unreferenced in AST)."

    formatted_blocks: list[str] = []
    for idx, u in enumerate(usages_list, 1):
        fpath = u.get("file_path", "unknown")
        lineno = u.get("line_number", 0)
        utype = u.get("usage_type", "usage")
        code = u.get("code_line", "").strip()
        context = u.get("context_snippet", "").strip()

        block = (
            f"Usage #{idx} ({utype}) in `{fpath}` at line {lineno}:\n"
            f"  Exact Line: {code}\n"
            f"  Surrounding Context:\n{context}\n"
        )
        formatted_blocks.append(block)

    return "\n".join(formatted_blocks)


def _construct_migration_prompt(
    pkg_name: str,
    pinned_version: str,
    latest_version: str,
    risk_indicators: list[str],
    test_failures: list[str],
    warnings: list[str],
    usages_text: str,
) -> str:
    """Build the comprehensive prompt for Gemma."""
    risks_str = "\n".join(f"- {r}" for r in risk_indicators) if risk_indicators else "- Deprecated or at-risk library"
    tests_str = "\n".join(f"- {tf}" for tf in test_failures) if test_failures else "None observed"
    warns_str = "\n".join(f"- {w}" for w in warnings) if warnings else "None observed"

    prompt = f"""Target Dependency: {pkg_name}
Pinned Version: {pinned_version}
Latest PyPI Version: {latest_version}

Risk Indicators:
{risks_str}

Observed Test Failures / Incompatibilities:
{tests_str}

Observed Runtime Warnings:
{warns_str}

AST Usages in Target Codebase:
{usages_text}

{KNOWN_MIGRATIONS_GUIDE}

Instructions:
Generate a precise, safe migration proposal to migrate away from this at-risk dependency on Python 3.12 (Windows).
Your response MUST be a single valid JSON object adhering strictly to this schema:
{{
  "action": "replace | upgrade | remove | keep",
  "replacement_package": "name of replacement package, or null if none/stdlib/remove",
  "replacement_version": "version specifier like '>=4.0.0', or null",
  "reasoning": "1-2 sentence concise explanation of why this migration is necessary",
  "migration_steps": [
    "Step 1...",
    "Step 2..."
  ],
  "code_edits": [
    {{
      "file": "relative/path/to/file.py",
      "old_text": "exact existing text in file",
      "new_text": "exact replacement text"
    }}
  ],
  "requirements_edits": {{
    "remove": ["package==x.y.z"],
    "add": ["replacement>=a.b.c"]
  }},
  "expected_gaps": [
    "Known limitations or behavioral differences"
  ],
  "suggested_tests": [
    "Descriptions of test cases to verify the migration"
  ]
}}

CRITICAL RULES:
1. `old_text` MUST match existing source text EXACTLY (including indentation and casing).
2. Do NOT use unified diffs or patch syntax (`---`, `+++`, `@@`).
3. If no code changes are required (e.g. for pure upgrade or unused library), `code_edits` MUST be `[]`.
4. Output ONLY valid JSON.
"""
    return prompt


def _validate_code_edits(
    edits: list[dict[str, Any]],
    repo_path: Path,
) -> list[dict[str, Any]]:
    """
    Validate that each code_edit's `old_text` actually exists in the target file.
    If not, log a warning with rich and discard that edit.
    """
    valid_edits: list[dict[str, Any]] = []

    for edit in edits:
        if not isinstance(edit, dict):
            continue

        file_rel = edit.get("file", "").strip()
        old_text = edit.get("old_text", "")
        new_text = edit.get("new_text", "")

        if not file_rel or not old_text:
            console.log(f"[yellow]Discarding invalid code edit with empty file or old_text: {edit}[/yellow]")
            continue

        target_file = (repo_path / file_rel).resolve()
        if not target_file.is_file():
            console.log(f"[bold yellow]Validation Warning:[/bold yellow] File '{file_rel}' not found on disk. Discarding edit.")
            continue

        try:
            content = target_file.read_text(encoding="utf-8", errors="replace")
        except Exception as read_err:
            console.log(f"[bold yellow]Validation Warning:[/bold yellow] Cannot read '{file_rel}': {read_err}. Discarding edit.")
            continue

        # Check existence with newline normalization to prevent Windows CRLF mismatches
        normalized_content = content.replace("\r\n", "\n")
        normalized_old = old_text.replace("\r\n", "\n")

        if normalized_old in normalized_content:
            valid_edits.append({
                "file": file_rel,
                "old_text": old_text,
                "new_text": new_text,
            })
        else:
            console.log(
                f"[bold yellow]Validation Warning:[/bold yellow] `old_text` does not exist in '{file_rel}'. "
                f"Discarding edit:\n[dim]{old_text!r}[/dim]"
            )

    return valid_edits


def _build_fallback_proposal(
    pkg_name: str,
    pinned_version: str,
    error_message: str,
) -> dict[str, Any]:
    """Create a standardized fallback proposal with action='manual_review' when LLM generation fails."""
    return {
        "dependency": pkg_name,
        "source": "AI-PROPOSED",
        "action": "manual_review",
        "replacement_package": None,
        "replacement_version": None,
        "reasoning": f"Automated proposal failed: {error_message}. Requires manual inspection.",
        "migration_steps": [
            f"Review {pkg_name} deprecation notices and compatibility with Python 3.12",
            "Manually inspect usages in codebase",
            "Select and test a modern alternative",
            "Update requirements.txt and verify test suite",
        ],
        "code_edits": [],
        "requirements_edits": {
            "remove": [],
            "add": [],
        },
        "expected_gaps": [
            "Automatic AST transformation could not be completed by LLM"
        ],
        "suggested_tests": [
            f"Run integration test suite covering {pkg_name}"
        ],
    }


def propose_migrations(
    at_risk_deps: list[Union[dict[str, Any], str]],
    usages: dict[str, list[dict[str, Any]]],
    risks: dict[str, Any],
    repo_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """
    Generate migration proposals for all at-risk dependencies.

    Args:
        at_risk_deps: List of at-risk dependency dicts (or dependency name strings).
        usages: Dict mapping package name -> list of AST usage records from usage_finder.
        risks: Dict mapping package name -> risk indicators, test failures, warnings.
        repo_path: Optional Path to target repository. Inferred if not provided.

    Returns:
        List of verified proposal dictionaries tagged with source='AI-PROPOSED'.
    """
    target_repo = resolve_target_repo(repo_path)
    proposals: list[dict[str, Any]] = []

    # Handle alternate parameter ordering if called as propose_migrations(discovery, risks, usages)
    if isinstance(at_risk_deps, dict) and "dependencies" in at_risk_deps:
        actual_deps = at_risk_deps.get("dependencies", [])
        actual_risks = usages if isinstance(usages, dict) else {}
        actual_usages = risks if isinstance(risks, dict) else {}
        at_risk_deps = actual_deps
        risks = actual_risks
        usages = actual_usages
    elif not isinstance(at_risk_deps, list):
        at_risk_deps = list(at_risk_deps) if at_risk_deps else []

    for item in at_risk_deps:
        # Normalize input item
        if isinstance(item, str):
            pkg_name = item
            pinned_ver = "unknown"
            latest_ver = "unknown"
            item_risks = []
        elif isinstance(item, dict):
            pkg_name = item.get("name") or item.get("package") or "unknown"
            pinned_ver = item.get("version") or item.get("pinned_version") or "unknown"
            latest_ver = item.get("latest_version") or "unknown"
            item_risks = item.get("reasons") or item.get("risk_indicators") or []
        else:
            continue

        console.log(f"[bold cyan]Analyzing migration for:[/bold cyan] [bold yellow]{pkg_name}[/bold yellow] ({pinned_ver})")

        # Gather package usages & risks
        pkg_usages = usages.get(pkg_name, [])
        risk_info = risks.get(pkg_name, {})
        if isinstance(risk_info, dict):
            combined_risks = list(set(item_risks + risk_info.get("risk_indicators", []) + risk_info.get("reasons", [])))
            test_failures = risk_info.get("test_failures", [])
            warnings = risk_info.get("warnings", [])
        elif isinstance(risk_info, list):
            combined_risks = list(set(item_risks + risk_info))
            test_failures = []
            warnings = []
        else:
            combined_risks = item_risks
            test_failures = []
            warnings = []

        usages_formatted = _format_usages_for_prompt(pkg_usages)

        # Build prompt
        prompt = _construct_migration_prompt(
            pkg_name=pkg_name,
            pinned_version=pinned_ver,
            latest_version=latest_ver,
            risk_indicators=combined_risks,
            test_failures=test_failures,
            warnings=warnings,
            usages_text=usages_formatted,
        )

        try:
            # Query LLM
            raw_response = ask_llm(prompt, system=GEMMA_SYSTEM_PROMPT)
            # Defensively extract JSON
            proposal = parse_json_from_llm(raw_response)

            # Ensure core fields exist
            proposal["dependency"] = pkg_name
            proposal["action"] = proposal.get("action", "manual_review")
            proposal["replacement_package"] = proposal.get("replacement_package")
            proposal["replacement_version"] = proposal.get("replacement_version")
            proposal["reasoning"] = proposal.get("reasoning", "")
            proposal["migration_steps"] = proposal.get("migration_steps", [])
            proposal["requirements_edits"] = proposal.get("requirements_edits", {"remove": [], "add": []})
            proposal["expected_gaps"] = proposal.get("expected_gaps", [])
            proposal["suggested_tests"] = proposal.get("suggested_tests", [])

            # Validate each code_edit against target repo files
            raw_edits = proposal.get("code_edits", [])
            valid_edits = _validate_code_edits(raw_edits, target_repo)
            proposal["code_edits"] = valid_edits

            # Tag proposal source as AI-PROPOSED
            proposal["source"] = "AI-PROPOSED"

            console.log(
                f"[bold green]Proposal generated for {pkg_name}:[/bold green] "
                f"Action=[bold magenta]{proposal['action']}[/bold magenta], "
                f"Valid Edits={len(valid_edits)}"
            )

        except Exception as exc:
            console.log(f"[bold red]Failed to generate proposal for {pkg_name}:[/bold red] {exc}")
            # Step 6: Return fallback proposal with action='manual_review'
            proposal = _build_fallback_proposal(pkg_name, pinned_ver, str(exc))

        proposals.append(proposal)

    # Print summary table
    print_proposals_summary(proposals)
    return proposals


def print_proposals_summary(proposals: list[dict[str, Any]]) -> None:
    """Print an attractive summary table of generated proposals."""
    table = Table(
        title="[bold green]GemmaMorph: Certified Migration Proposals (Stage 4)[/bold green]",
        box=box.ROUNDED,
        header_style="bold magenta",
        title_style="bold green",
    )
    table.add_column("Dependency", style="bold cyan", min_width=15)
    table.add_column("Action", justify="center", min_width=12)
    table.add_column("Replacement", style="bold yellow", min_width=18)
    table.add_column("Code Edits", justify="center", width=12)
    table.add_column("Source", justify="center", width=15)
    table.add_column("Reasoning", style="dim", min_width=30)

    for p in proposals:
        act = p.get("action", "unknown").lower()
        if act in ("replace", "upgrade"):
            act_styled = f"[bold green]{act.upper()}[/bold green]"
        elif act == "remove":
            act_styled = f"[bold red]{act.upper()}[/bold red]"
        else:
            act_styled = f"[bold yellow]{act.upper()}[/bold yellow]"

        rep = p.get("replacement_package") or "-"
        ver = p.get("replacement_version")
        if ver and rep != "-":
            rep = f"{rep} ({ver})"

        edits_count = str(len(p.get("code_edits", [])))
        src = f"[bold]{p.get('source', 'AI-PROPOSED')}[/bold]"
        reasoning = p.get("reasoning", "")
        if len(reasoning) > 40:
            reasoning = reasoning[:37] + "..."

        table.add_row(
            p.get("dependency", "unknown"),
            act_styled,
            rep,
            edits_count,
            src,
            reasoning,
        )

    console.print(table)


if __name__ == "__main__":
    from depexit.usage_finder import find_usages

    repo = resolve_target_repo()
    test_deps = [
        {"name": "appdirs", "version": "1.4.4", "latest_version": "1.4.4", "reasons": ["Unmaintained since 2017"]},
        {"name": "six", "version": "1.17.0", "latest_version": "1.17.0", "reasons": ["Obsolete Python 2 compatibility shim"]},
    ]

    console.print(f"[bold blue]Target repository:[/bold blue] {repo}")
    dep_names = [d["name"] for d in test_deps]
    usages_result = find_usages(repo, dep_names)
    test_risks = {
        "appdirs": {"risk_indicators": ["Unmaintained since 2017", "Use platformdirs instead"]},
        "six": {"risk_indicators": ["Legacy Python 2 shim", "Zero usages"]},
    }

    results = propose_migrations(test_deps, usages_result, test_risks, repo_path=repo)
    console.print(f"[bold green]Generated {len(results)} proposals successfully.[/bold green]")
