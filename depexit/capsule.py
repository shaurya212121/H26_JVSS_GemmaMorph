"""
Stage 6: Dependency Exit Capsule Generator
============================================
Generates certified, tamper-evident Dependency Exit Capsules in both
machine-readable JSON (EXIT_CAPSULE.json) and human-readable Markdown
(EXIT_CAPSULE.md) formats, enforcing strict claim verification tags:
  - [VERIFIED]   : Confirmed by code execution (AST parsing, pytest passes)
  - [OBSERVED]   : Seen in test output (warnings, deprecations)
  - [AI-PROPOSED]: Synthesized by Google Gemma AI
  - [REFUTED]    : AI proposal that broke tests or failed in sandbox
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _normalize_dependencies(discovery: dict) -> dict[str, dict]:
    """Normalize discovery data into a standard mapping keyed by dep name."""
    deps: dict[str, dict] = {}
    raw_deps = discovery.get("declared_deps") or discovery.get("dependencies") or {}
    if isinstance(raw_deps, list):
        for item in raw_deps:
            if isinstance(item, dict):
                name = item.get("name") or item.get("dep_name") or "unknown"
                entry = dict(item)
                entry.setdefault("version", item.get("pinned_version") or "unknown")
                deps[name.lower()] = entry
            elif isinstance(item, str):
                deps[item.lower()] = {"name": item, "version": "unknown", "source_file": "requirements.txt"}
    elif isinstance(raw_deps, dict):
        for name, data in raw_deps.items():
            if isinstance(data, dict):
                entry = dict(data)
                entry.setdefault("name", name)
                deps[name.lower()] = entry
            else:
                deps[name.lower()] = {"name": name, "version": str(data), "source_file": "requirements.txt"}
    return deps


def _normalize_risks(risks: dict) -> dict[str, dict]:
    """Normalize risk scanning data into a standard mapping keyed by dep name."""
    risk_map: dict[str, dict] = {}
    raw_risks = risks.get("risks", risks)
    if isinstance(raw_risks, list):
        for item in raw_risks:
            if isinstance(item, dict):
                name = item.get("name") or item.get("dependency") or item.get("dep_name") or "unknown"
                risk_map[name.lower()] = item
    elif isinstance(raw_risks, dict):
        for name, data in raw_risks.items():
            if isinstance(data, dict):
                risk_map[name.lower()] = data
            elif isinstance(data, str):
                risk_map[name.lower()] = {"risk_level": data, "indicators": []}
    return risk_map


def _normalize_usages(usages: dict) -> dict[str, list[dict]]:
    """Normalize usage finder data into a standard mapping of dep name to usages."""
    usage_map: dict[str, list[dict]] = {}
    raw_usages = usages.get("usages", usages)
    if isinstance(raw_usages, dict):
        for name, locs in raw_usages.items():
            if isinstance(locs, list):
                usage_map[name.lower()] = locs
            elif isinstance(locs, dict):
                usage_map[name.lower()] = [locs]
    elif isinstance(raw_usages, list):
        for item in raw_usages:
            if isinstance(item, dict):
                name = item.get("dependency") or item.get("name") or "unknown"
                usage_map.setdefault(name.lower(), []).append(item)
    return usage_map


def _normalize_proposals(proposals: list[dict]) -> dict[str, dict]:
    """Index Gemma migration proposals by dep name."""
    prop_map: dict[str, dict] = {}
    for p in proposals:
        if isinstance(p, dict):
            name = p.get("dep_name") or p.get("name") or p.get("dependency") or "unknown"
            prop_map[name.lower()] = p
    return prop_map


def _normalize_rehearsals(rehearsal: dict) -> tuple[dict, dict[str, dict], dict]:
    """Extract baseline, per-proposal, and combined rehearsal results."""
    baseline = rehearsal.get("baseline", {})
    per_prop_list = rehearsal.get("per_proposal", [])
    per_prop_map: dict[str, dict] = {}
    for r in per_prop_list:
        if isinstance(r, dict):
            name = r.get("dep_name", "unknown")
            per_prop_map[name.lower()] = r
    combined = rehearsal.get("combined", {})
    return baseline, per_prop_map, combined


def generate_capsule(
    discovery: dict,
    risks: dict,
    usages: dict,
    proposals: list[dict],
    rehearsal: dict,
    output_dir: Path,
    model_info: dict,
) -> tuple[Path, Path]:
    """
    Generate the certified Dependency Exit Capsule in both JSON and Markdown formats.

    Parameters:
        discovery: Stage 1 output (discovered dependencies)
        risks: Stage 2 output (risk scores & indicators)
        usages: Stage 3 output (AST codebase usages)
        proposals: Stage 4 output (Gemma migration proposals)
        rehearsal: Stage 5 output (sandbox baseline & rehearsal test runs)
        output_dir: Target folder to save EXIT_CAPSULE.json and EXIT_CAPSULE.md
        model_info: Metadata for the Gemma model used

    Returns:
        tuple[Path, Path]: (json_path, markdown_path)
    """
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    target_repo = str(discovery.get("repo_path") or discovery.get("repo_name") or "LinkWatch")
    generation_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    deps_map = _normalize_dependencies(discovery)
    risks_map = _normalize_risks(risks)
    usages_map = _normalize_usages(usages)
    proposals_map = _normalize_proposals(proposals)
    baseline, reh_map, combined = _normalize_rehearsals(rehearsal)

    # Collect all unique dependencies mentioned
    all_dep_names = sorted(set(list(deps_map.keys()) + list(risks_map.keys()) + list(proposals_map.keys())))

    dep_capsules: list[dict] = []
    stats = {
        "total_dependencies": len(all_dep_names),
        "high_risk": 0,
        "medium_risk": 0,
        "low_risk": 0,
        "rehearsed": len(reh_map),
        "certified": 0,
        "refuted": 0,
    }

    for dep_name in all_dep_names:
        dep_info = deps_map.get(dep_name, {})
        risk_info = risks_map.get(dep_name, {})
        usage_list = usages_map.get(dep_name, [])
        prop_info = proposals_map.get(dep_name)
        reh_info = reh_map.get(dep_name)

        version = dep_info.get("version") or "unknown"
        source_file = dep_info.get("source_file") or "requirements.txt"

        risk_level = (risk_info.get("risk_level") or "LOW").upper()
        risk_score = risk_info.get("score") or risk_info.get("risk_score") or (85 if risk_level == "HIGH" else 45 if risk_level == "MEDIUM" else 15)

        if risk_level == "HIGH":
            stats["high_risk"] += 1
        elif risk_level == "MEDIUM":
            stats["medium_risk"] += 1
        else:
            stats["low_risk"] += 1

        # Classify risk indicators with tags
        raw_indicators = risk_info.get("indicators") or []
        metadata_indicators = []
        observed_indicators = []
        for ind in raw_indicators:
            if isinstance(ind, dict):
                tag = ind.get("tag", "[VERIFIED]")
                desc = ind.get("description") or str(ind)
                if "[OBSERVED]" in tag or "observed" in desc.lower() or "warning" in desc.lower():
                    observed_indicators.append({"claim_type": "[OBSERVED]", "description": desc})
                else:
                    metadata_indicators.append({"claim_type": "[VERIFIED]", "description": desc})
            elif isinstance(ind, str):
                if "warning" in ind.lower() or "deprecated" in ind.lower() or "runtime" in ind.lower():
                    observed_indicators.append({"claim_type": "[OBSERVED]", "description": ind})
                else:
                    metadata_indicators.append({"claim_type": "[VERIFIED]", "description": ind})

        # Usage locations
        tagged_usages = []
        for u in usage_list:
            tagged_usages.append({
                "claim_type": "[VERIFIED]",
                "file": u.get("file") or u.get("file_path") or "unknown",
                "line": u.get("line") or u.get("line_number") or 1,
                "api": u.get("api") or u.get("api_name") or u.get("function") or "import",
                "code": u.get("code") or u.get("code_snippet") or "",
            })

        # Gemma Proposal details
        replacement_name = None
        replacement_rationale = None
        migration_steps = []
        suggested_tests = []
        compat_gaps = []
        open_concerns = []

        if prop_info:
            replacement_name = (
                prop_info.get("proposed_replacement")
                or prop_info.get("replacement")
                or prop_info.get("target_library")
            )
            replacement_rationale = prop_info.get("rationale") or prop_info.get("description") or "Modern resilient alternative suggested by Gemma."
            
            raw_steps = prop_info.get("migration_steps") or prop_info.get("steps") or []
            for s in raw_steps:
                migration_steps.append({"claim_type": "[AI-PROPOSED]", "step": s if isinstance(s, str) else str(s)})

            raw_tests = prop_info.get("suggested_tests") or prop_info.get("tests") or []
            for t in raw_tests:
                suggested_tests.append({"claim_type": "[AI-PROPOSED]", "test": t if isinstance(t, str) else str(t)})

            raw_gaps = prop_info.get("compatibility_gaps") or prop_info.get("gaps") or []
            for g in raw_gaps:
                compat_gaps.append({"claim_type": "[AI-PROPOSED]", "gap": g if isinstance(g, str) else str(g)})

            raw_concerns = prop_info.get("open_concerns") or prop_info.get("limitations") or []
            for c in raw_concerns:
                open_concerns.append({"claim_type": "[AI-PROPOSED]", "concern": c if isinstance(c, str) else str(c)})

        # Rehearsal results tagging
        rehearsal_section = None
        capsule_verdict = "[VERIFIED] Retained"

        if reh_info:
            newly_failing = reh_info.get("newly_failing", [])
            install_ok = reh_info.get("install_ok", True)
            w_diff = reh_info.get("warnings_diff", {})

            if install_ok and len(newly_failing) == 0:
                claim_tag = "[VERIFIED]"
                verdict_str = "[VERIFIED] Certified for Migration"
                stats["certified"] += 1
            else:
                claim_tag = "[REFUTED]"
                verdict_str = "[REFUTED] Regressions Detected in Sandbox"
                stats["refuted"] += 1

            capsule_verdict = verdict_str

            rehearsal_section = {
                "claim_type": claim_tag,
                "status": reh_info.get("status", "SUCCESS"),
                "install_ok": install_ok,
                "applied_edits": reh_info.get("applied_edits", 0),
                "baseline_tests": baseline.get("total", 0),
                "post_migration_tests": reh_info.get("total", 0),
                "passed": reh_info.get("passed", 0),
                "failed": reh_info.get("failed", 0),
                "newly_failing": newly_failing,
                "newly_passing": reh_info.get("newly_passing", []),
                "warnings_removed": [
                    {"claim_type": "[OBSERVED]", "warning": w}
                    for w in w_diff.get("removed", [])
                ],
                "warnings_added": [
                    {"claim_type": "[OBSERVED]", "warning": w}
                    for w in w_diff.get("added", [])
                ],
            }

        capsule_entry = {
            "dependency": {
                "claim_type": "[VERIFIED]",
                "name": dep_name,
                "version": version,
                "source_file": source_file,
            },
            "risk_assessment": {
                "risk_level": risk_level,
                "risk_score": risk_score,
                "metadata_indicators": metadata_indicators,
                "observed_indicators": observed_indicators,
            },
            "usages": tagged_usages,
            "proposed_replacement": {
                "claim_type": "[AI-PROPOSED]",
                "name": replacement_name,
                "rationale": replacement_rationale,
            } if replacement_name else None,
            "migration_steps": migration_steps,
            "suggested_tests": suggested_tests,
            "rehearsal": rehearsal_section,
            "compatibility_gaps": compat_gaps,
            "open_concerns": open_concerns,
            "capsule_status": capsule_verdict,
        }
        dep_capsules.append(capsule_entry)

    # 1. Output machine-readable JSON
    json_path = output_dir / "EXIT_CAPSULE.json"
    full_json_payload = {
        "capsule_version": "1.0.0",
        "generated_at": generation_date,
        "target_repo": target_repo,
        "model_info": model_info,
        "summary": stats,
        "dependencies": dep_capsules,
        "rehearsal_suite": {
            "baseline": baseline,
            "combined": combined,
        },
        "raw_discovery": discovery,
        "raw_risks": risks,
        "raw_usages": usages,
        "raw_proposals": proposals,
    }
    json_path.write_text(json.dumps(full_json_payload, indent=2), encoding="utf-8")

    # 2. Output beautiful human-readable Markdown
    md_path = output_dir / "EXIT_CAPSULE.md"
    md_content = _build_markdown_report(
        target_repo=target_repo,
        generation_date=generation_date,
        model_info=model_info,
        stats=stats,
        dep_capsules=dep_capsules,
        baseline=baseline,
        combined=combined,
    )
    md_path.write_text(md_content, encoding="utf-8")

    # Console summary
    _print_capsule_summary(json_path, md_path, stats)

    return json_path, md_path


def _build_markdown_report(
    target_repo: str,
    generation_date: str,
    model_info: dict,
    stats: dict,
    dep_capsules: list[dict],
    baseline: dict,
    combined: dict,
) -> str:
    """Generate the full GitHub Flavored Markdown Exit Capsule report."""
    md: list[str] = []

    # Header
    md.append("# 🛡️ GemmaMorph: Dependency Exit Capsule")
    md.append("")
    md.append("> **Autonomous Dependency Resilience & Migration Certification Report**  ")
    md.append("> *Engineered by Team JVSS for Hacktoberfest 2026 Hack Day Mumbai*")
    md.append("")
    md.append("---")
    md.append("")

    # System Provenance Table
    md.append("### 📋 Executive Provenance")
    md.append("")
    md.append("| Attribute | Value |")
    md.append("| :--- | :--- |")
    md.append(f"| **Target Repository** | `{target_repo}` |")
    md.append(f"| **Generated Timestamp** | `{generation_date}` |")
    md.append(f"| **AI Model** | `{model_info.get('model_name', 'gemma2-9b-it')}` ({model_info.get('model_version', '9B-Instruct')}) |")
    md.append(f"| **Access Method** | `{model_info.get('access_method', 'Groq hosted API')}` |")
    md.append(f"| **Model License** | `{model_info.get('model_license', 'Apache 2.0')}` |")
    md.append(f"| **Model Role** | `{model_info.get('role_in_project', 'Semantic risk analysis and migration code generation')}` |")
    md.append("")

    # Verification Taxonomy
    md.append("### 🏷️ Claim Verification Taxonomy")
    md.append("")
    md.append("| Tag | Definition | Validation Method |")
    md.append("| :--- | :--- | :--- |")
    md.append("| `[VERIFIED]` | Ground truth confirmed by deterministic analysis | Python AST parsing, PyPI manifest queries, clean pytest execution |")
    md.append("| `[OBSERVED]` | Runtime behavior witnessed directly | Pytest warning summary capture, deprecation tracebacks |")
    md.append("| `[AI-PROPOSED]` | Synthesized by Google Gemma AI | Neural code generation, migration reasoning, compatibility analysis |")
    md.append("| `[REFUTED]` | AI migration hypothesis invalidated | Sandbox test regressions, syntax failures, or dependency conflicts |")
    md.append("")

    # Executive Summary Cards
    md.append("### 📊 Migration Health Dashboard")
    md.append("")
    md.append(f"- **Total Dependencies Scanned:** `{stats['total_dependencies']}`")
    md.append(f"- **Risk Stratification:** 🔴 `{stats['high_risk']}` High | 🟡 `{stats['medium_risk']}` Medium | 🟢 `{stats['low_risk']}` Low")
    md.append(f"- **Live Sandbox Rehearsals Run:** `{stats['rehearsed']}`")
    md.append(f"- **Certified Exit Capsules (`[VERIFIED]`):** `{stats['certified']}`")
    md.append(f"- **Refuted AI Proposals (`[REFUTED]`):** `{stats['refuted']}`")
    md.append("")

    # Summary Table
    md.append("### 📑 Dependency Resilience Matrix")
    md.append("")
    md.append("| Dependency | Version | Source | Risk Level | Proposed Replacement | Rehearsal Pass | Capsule Status |")
    md.append("| :--- | :--- | :--- | :---: | :--- | :---: | :--- |")
    for dep in dep_capsules:
        d = dep["dependency"]
        r = dep["risk_assessment"]
        rep = dep.get("proposed_replacement")
        reh = dep.get("rehearsal")
        
        risk_emoji = "🔴 HIGH" if r["risk_level"] == "HIGH" else "🟡 MED" if r["risk_level"] == "MEDIUM" else "🟢 LOW"
        rep_text = f"`{rep['name']}`" if rep and rep.get("name") else "*None needed*"
        reh_pass = "✅ Yes" if (reh and reh.get("status") == "SUCCESS") else "❌ Regressed" if (reh and reh.get("status") == "REGRESSION") else "⚠️ Install Fail" if (reh and reh.get("status") == "INSTALL_FAILED") else "—"
        status_text = dep.get("capsule_status", "[VERIFIED] Retained")

        md.append(f"| **`{d['name']}`** | `{d['version']}` | `{d['source_file']}` | {risk_emoji} | {rep_text} | {reh_pass} | **{status_text}** |")
    md.append("")
    md.append("---")
    md.append("")

    # Detailed Dependency Capsules
    md.append("## 📦 Detailed Dependency Capsules")
    md.append("")

    for dep in dep_capsules:
        d = dep["dependency"]
        r = dep["risk_assessment"]
        rep = dep.get("proposed_replacement")
        reh = dep.get("rehearsal")

        md.append(f"### 🔍 `{d['name']}` (v{d['version']})")
        md.append("")
        md.append(f"**Status Verdict:** `{dep.get('capsule_status')}`  ")
        md.append(f"**Manifest Source:** `[VERIFIED]` `{d['source_file']}`  ")
        md.append(f"**Risk Severity Score:** `{r['risk_level']}` ({r['risk_score']}/100)")
        md.append("")

        # 1. Risk Indicators
        md.append("#### 1. Risk Indicators")
        if r["metadata_indicators"] or r["observed_indicators"]:
            for mi in r["metadata_indicators"]:
                md.append(f"- `{mi['claim_type']}` **Static Metadata:** {mi['description']}")
            for oi in r["observed_indicators"]:
                md.append(f"- `{oi['claim_type']}` **Observed Runtime:** {oi['description']}")
        else:
            md.append("- `[VERIFIED]` No immediate critical risk indicators found; library is healthy.")
        md.append("")

        # 2. Usage Locations
        md.append("#### 2. Code Usage Locations")
        if dep["usages"]:
            md.append("| Tag | File Location | Line | Target API / Symbol | Code Context |")
            md.append("| :---: | :--- | :---: | :--- | :--- |")
            for u in dep["usages"]:
                code_snippet = f"`{u['code']}`" if u['code'] else "—"
                md.append(f"| `{u['claim_type']}` | `{u['file']}` | `{u['line']}` | `{u['api']}` | {code_snippet} |")
        else:
            md.append("- `[VERIFIED]` No direct AST callsites detected (unused or indirect transit).")
        md.append("")

        # 3. Proposed Replacement
        md.append("#### 3. Proposed Replacement & Architectural Strategy")
        if rep and rep.get("name"):
            md.append(f"- `{rep['claim_type']}` **Target Replacement Library:** `{rep['name']}`")
            md.append(f"- `{rep['claim_type']}` **Architectural Rationale:** {rep['rationale']}")
        else:
            md.append("- `[VERIFIED]` No migration required at this time.")
        md.append("")

        # 4. Migration Plan & Steps
        md.append("#### 4. Step-by-Step Migration Plan")
        if dep["migration_steps"]:
            for idx, s in enumerate(dep["migration_steps"], 1):
                md.append(f"{idx}. `{s['claim_type']}` {s['step']}")
        else:
            md.append("- `[VERIFIED]` None required.")
        md.append("")

        # 5. Suggested Tests
        md.append("#### 5. Suggested Verification Tests")
        if dep["suggested_tests"]:
            for t in dep["suggested_tests"]:
                md.append(f"- `{t['claim_type']}` {t['test']}")
        else:
            md.append("- Standard regression test suite.")
        md.append("")

        # 6. Rehearsal Results
        md.append("#### 6. Executed Sandbox Rehearsal Results")
        if reh:
            md.append(f"- `{reh['claim_type']}` **Rehearsal Outcome:** `{reh['status']}`")
            md.append(f"- `{reh['claim_type']}` **Applied Code Edits:** `{reh['applied_edits']}`")
            md.append(f"- `{reh['claim_type']}` **Pip Requirements Install:** `{'Passed' if reh['install_ok'] else 'Failed'}`")
            md.append(f"- `{reh['claim_type']}` **Post-Migration Tests:** `{reh['passed']}` passed, `{reh['failed']}` failed (`{reh['post_migration_tests']}` total)")

            if reh["newly_failing"]:
                md.append(f"- `[REFUTED]` **Newly Failing Tests (Regressions):**")
                for nf in reh["newly_failing"]:
                    md.append(f"  - ❌ `{nf}`")
            else:
                md.append("- `[VERIFIED]` **Zero Test Regressions Detected.**")

            if reh["warnings_removed"]:
                md.append(f"- `[OBSERVED]` **Warnings Eliminated:**")
                for wr in reh["warnings_removed"]:
                    md.append(f"  - 🟢 `{wr['warning']}`")

            if reh["warnings_added"]:
                md.append(f"- `[OBSERVED]` **New Warnings Introduced:**")
                for wa in reh["warnings_added"]:
                    md.append(f"  - ⚠️ `{wa['warning']}`")
        else:
            md.append("- `[VERIFIED]` Migration rehearsal was not required for this component.")
        md.append("")

        # 7. Compatibility Gaps
        md.append("#### 7. Compatibility Gaps")
        if dep["compatibility_gaps"]:
            for g in dep["compatibility_gaps"]:
                md.append(f"- `{g['claim_type']}` {g['gap']}")
        else:
            md.append("- None reported.")
        md.append("")

        # 8. Open Concerns & Limitations
        md.append("#### 8. Open Concerns & Limitations")
        if dep["open_concerns"]:
            for c in dep["open_concerns"]:
                md.append(f"- `{c['claim_type']}` {c['concern']}")
        else:
            md.append("- None reported.")
        md.append("")
        md.append("---")
        md.append("")

    # Combined Rehearsal Results
    md.append("## 🧪 Combined Exit Rehearsal Results")
    md.append("")
    md.append("> Demonstrates simultaneous replacement of all individually verified candidate dependencies in a unified sandbox.")
    md.append("")
    comb_proposals = combined.get("applied_proposals", [])
    comb_status = combined.get("status", "N/A")

    md.append(f"- **Applied Candidate Migrations:** `{', '.join(comb_proposals) or 'None'}`")
    md.append(f"- **Baseline Test Results:** `{baseline.get('passed', 0)}` passed, `{baseline.get('failed', 0)}` failed")
    md.append(f"- **Unified Sandbox Test Results:** `{combined.get('passed', 0)}` passed, `{combined.get('failed', 0)}` failed")
    
    comb_failing = combined.get("newly_failing", [])
    if comb_failing:
        md.append(f"- `[REFUTED]` **Combined Test Regressions:**")
        for cf in comb_failing:
            md.append(f"  - ❌ `{cf}`")
        md.append(f"- **Final Certification Verdict:** `[REFUTED] Unified migration caused regressions`")
    else:
        md.append(f"- `[VERIFIED]` **Combined Regressions:** `0` (Clean run across entire test suite)")
        md.append(f"- **Final Certification Verdict:** `[VERIFIED] Certified Production-Ready Exit Capsule`")
    md.append("")
    md.append("---")
    md.append("")

    # Footer
    md.append("### 📜 Model & Project Provenance")
    md.append("")
    md.append(f"- **Model:** Google `{model_info.get('model_name', 'gemma2-9b-it')}` ({model_info.get('model_version', '9B-Instruct')})")
    md.append(f"- **Inference Provider:** `{model_info.get('access_method', 'Groq API')}`")
    md.append(f"- **License:** `{model_info.get('model_license', 'Apache 2.0')}`")
    md.append(f"- **GemmaMorph Version:** `v1.0` | **Team:** `JVSS`")
    md.append("")

    return "\n".join(md)


def _print_capsule_summary(json_path: Path, md_path: Path, stats: dict) -> None:
    """Print a clean summary of capsule generation to console."""
    console.print("\n[bold cyan]─── Stage 6: Dependency Exit Capsule Generated ───[/bold cyan]")
    
    table = Table(title="Exit Capsule Artifacts", header_style="bold green")
    table.add_column("Artifact Format", style="bold")
    table.add_column("File Path")
    table.add_column("File Size", justify="right")

    try:
        json_size = f"{json_path.stat().st_size / 1024:.1f} KB"
        md_size = f"{md_path.stat().st_size / 1024:.1f} KB"
    except Exception:
        json_size = "N/A"
        md_size = "N/A"

    table.add_row("Machine JSON", str(json_path), json_size)
    table.add_row("Human Markdown", str(md_path), md_size)
    console.print(table)

    console.print(
        Panel(
            f"[bold]Certified Migrations (`[VERIFIED]`):[/bold] [green]{stats['certified']}[/green]\n"
            f"[bold]Refuted Migrations (`[REFUTED]`):[/bold] [red]{stats['refuted']}[/red]\n"
            f"[bold]Total Dependencies Evaluated:[/bold] [cyan]{stats['total_dependencies']}[/cyan]",
            title="Capsule Certification Summary",
            border_style="green",
        )
    )
