#!/usr/bin/env python3
"""
GemmaMorph: Autonomous Dependency Resilience & Exit Rehearsal Engine
====================================================================
Main entry point and orchestration pipeline.

Usage:
    python run.py --repo ../LinkWatch [--hosted] [--keep-sandbox] [--skip-rehearsal] [--output ./output]

Stages:
    Stage 1: Dependency Discovery (discover)
    Stage 2: Risk Scanning (scan_risks)
    Stage 3: AST Usage Analysis (find_usages)
    Stage 4: Gemma AI Migration Proposals (propose_migrations)
    Stage 5: Exit Rehearsal in Sandbox Clones (run_rehearsal)
    Stage 6: Dependency Exit Capsule Generation (generate_capsule)
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env file early if present
try:
    from dotenv import find_dotenv, load_dotenv
    _env_file = _PROJECT_ROOT / ".env"
    if _env_file.exists():
        load_dotenv(dotenv_path=_env_file, override=False)
    else:
        load_dotenv(find_dotenv(), override=False)
except ImportError:
    pass

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
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


def parse_arguments() -> argparse.Namespace:
    """Parse and validate command line arguments."""
    parser = argparse.ArgumentParser(
        prog="GemmaMorph",
        description="Autonomous Dependency Resilience & Exit Rehearsal Engine (Team JVSS | Hacktoberfest 2026)",
    )
    parser.add_argument(
        "--repo",
        type=str,
        required=True,
        help="Path to the target repository to analyze (e.g. ../LinkWatch)",
    )
    parser.add_argument(
        "--hosted",
        action="store_true",
        default=False,
        help="Use hosted Groq API (Gemma2-9B-IT) instead of local Ollama (Gemma2-2B)",
    )
    parser.add_argument(
        "--keep-sandbox",
        action="store_true",
        default=False,
        help="Do not delete temporary sandbox directories after rehearsal",
    )
    parser.add_argument(
        "--skip-rehearsal",
        action="store_true",
        default=False,
        help="Skip Stage 5 sandbox execution for rapid dry-run demonstrations",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="./output",
        help="Target directory for generated exit capsules (default: ./output)",
    )
    return parser.parse_args()


def print_banner() -> None:
    """Display the official GemmaMorph banner."""
    banner_text = (
        "[bold cyan]╔══════════════════════════════════════════╗[/bold cyan]\n"
        "[bold cyan]║[/bold cyan]  [bold white]GemmaMorph v1.0 — Exit Rehearsal Engine[/bold white] [bold cyan]║[/bold cyan]\n"
        "[bold cyan]║[/bold cyan]  [bold yellow]Team JVSS | Hacktoberfest 2026[/bold yellow]          [bold cyan]║[/bold cyan]\n"
        "[bold cyan]╚══════════════════════════════════════════╝[/bold cyan]"
    )
    console.print(Panel(banner_text, border_style="bright_blue", expand=False))


def main() -> int:
    start_time = time.time()
    args = parse_arguments()

    # Step 2: Configure environment based on execution flags
    if args.hosted:
        os.environ["LLM_BACKEND"] = "groq"
    else:
        os.environ.setdefault("LLM_BACKEND", "ollama")

    print_banner()

    repo_path = Path(args.repo).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold]Target Repository:[/bold] [cyan]{repo_path}[/cyan]")
    console.print(f"[bold]LLM Backend:[/bold]       [magenta]{os.environ.get('LLM_BACKEND', 'ollama').upper()}[/magenta]")
    console.print(f"[bold]Output Directory:[/bold]  [yellow]{output_dir}[/yellow]")
    console.print(f"[bold]Keep Sandboxes:[/bold]    [dim]{args.keep_sandbox}[/dim]")
    console.print(f"[bold]Skip Rehearsal:[/bold]    [dim]{args.skip_rehearsal}[/dim]\n")

    if not repo_path.exists():
        console.print(Panel(f"[bold red]Error:[/bold red] Target repository '{repo_path}' does not exist.", border_style="red"))
        return 1

    discovery: dict = {}
    risks: dict = {}
    usages: dict = {}
    proposals: list[dict] = []
    rehearsal: dict = {}

    # Stage 1: Dependency Discovery
    try:
        from depexit.discover import discover
        console.print("[bold cyan]─── Stage 1: Dependency Discovery ───[/bold cyan]")
        discovery = discover(repo_path)
    except Exception as exc:
        console.print(
            Panel(
                f"[bold red]Stage 1 Discovery Failed:[/bold red] {exc}",
                title="Stage 1 Error",
                border_style="red",
            )
        )
        discovery = {"repo_path": str(repo_path), "dependencies": []}

    # Stage 2: Risk Scanning
    try:
        from depexit.risk_scan import scan_risks
        console.print("\n[bold cyan]─── Stage 2: Risk Scanning ───[/bold cyan]")
        sig = inspect.signature(scan_risks)
        if len(sig.parameters) == 1:
            risks = scan_risks(discovery)
        else:
            risks = scan_risks(repo_path, discovery)
    except Exception as exc:
        console.print(
            Panel(
                f"[bold red]Stage 2 Risk Scan Failed:[/bold red] {exc}",
                title="Stage 2 Error",
                border_style="red",
            )
        )
        risks = {"risks": {}}

    # Stage 3: AST Usage Analysis
    try:
        from depexit.usage_finder import find_usages
        console.print("\n[bold cyan]─── Stage 3: AST Usage Finder ───[/bold cyan]")
        
        # Collect dependency list for usage search
        dep_names: list[str] = []
        raw_deps = discovery.get("declared_deps") or discovery.get("dependencies") or []
        if isinstance(raw_deps, list):
            for d in raw_deps:
                if isinstance(d, dict):
                    name = d.get("name") or d.get("dep_name")
                    if name:
                        dep_names.append(name)
                elif isinstance(d, str):
                    dep_names.append(d)
        elif isinstance(raw_deps, dict):
            dep_names = list(raw_deps.keys())

        if not dep_names and isinstance(risks.get("risks"), dict):
            dep_names = list(risks["risks"].keys())

        # If still empty, fall back to searching requirements.txt directly
        if not dep_names:
            req_file = repo_path / "requirements.txt"
            if req_file.exists():
                for line in req_file.read_text(encoding="utf-8").splitlines():
                    cleaned = line.strip().split("#")[0].strip()
                    if cleaned:
                        import re
                        base = re.split(r"[=<>!~]", cleaned)[0].strip()
                        if base:
                            dep_names.append(base)

        dep_names = sorted(list(set(dep_names)))
        usages = find_usages(repo_path, dep_names)
    except Exception as exc:
        console.print(
            Panel(
                f"[bold red]Stage 3 Usage Finder Failed:[/bold red] {exc}",
                title="Stage 3 Error",
                border_style="red",
            )
        )
        usages = {}

    # Stage 4: Gemma AI Migration Proposals
    try:
        from depexit.gemma_propose import propose_migrations
        console.print("\n[bold cyan]─── Stage 4: Gemma AI Migration Proposals ───[/bold cyan]")
        
        sig = inspect.signature(propose_migrations)
        param_names = list(sig.parameters.keys())
        
        if "at_risk_deps" in param_names:
            # Prepare at_risk list
            at_risk: list[dict] = []
            if isinstance(risks.get("risks"), dict):
                for k, v in risks["risks"].items():
                    if isinstance(v, dict):
                        level = v.get("risk_level", "").upper()
                        if level in ("HIGH", "MEDIUM"):
                            at_risk.append({"name": k, **v})
                    elif isinstance(v, str) and v.upper() in ("HIGH", "MEDIUM"):
                        at_risk.append({"name": k, "risk_level": v})
            
            # If no classified risks yet, pass all discovered dependencies
            if not at_risk:
                at_risk = [{"name": d} for d in dep_names]

            proposals = propose_migrations(at_risk, usages, risks, repo_path=repo_path)
        else:
            proposals = propose_migrations(discovery, risks, usages)
    except Exception as exc:
        console.print(
            Panel(
                f"[bold red]Stage 4 Proposal Generation Failed:[/bold red] {exc}",
                title="Stage 4 Error",
                border_style="red",
            )
        )
        proposals = []

    # Stage 5: Exit Rehearsal in Sandbox
    if args.skip_rehearsal:
        console.print("\n[bold yellow]─── Stage 5: Skipped (--skip-rehearsal flag enabled) ───[/bold yellow]")
        rehearsal = {
            "baseline": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "test_details": [],
                "passed_tests": [],
                "failed_tests": [],
                "warnings": [],
            },
            "per_proposal": [],
            "combined": {
                "applied_proposals": [],
                "total": 0,
                "passed": 0,
                "failed": 0,
                "newly_failing": [],
                "status": "SKIPPED",
            },
        }
    else:
        try:
            from depexit.rehearsal import run_rehearsal
            rehearsal = run_rehearsal(
                repo_path=repo_path,
                proposals=proposals,
                keep_sandbox=args.keep_sandbox,
            )
        except Exception as exc:
            console.print(
                Panel(
                    f"[bold red]Stage 5 Exit Rehearsal Failed:[/bold red] {exc}",
                    title="Stage 5 Error",
                    border_style="red",
                )
            )
            rehearsal = {
                "baseline": {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "test_details": [],
                    "passed_tests": [],
                    "failed_tests": [],
                    "warnings": [],
                },
                "per_proposal": [],
                "combined": {
                    "applied_proposals": [],
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "newly_failing": [],
                    "status": "ERROR",
                },
            }

    # Stage 6: Dependency Exit Capsule Generator
    model_name = os.getenv("GEMMA_MODEL", "gemma2-9b-it" if args.hosted else "gemma2:2b")
    model_ver = "9B-Instruct" if "9b" in model_name.lower() else "2B-Instruct" if "2b" in model_name.lower() else "Gemma-2"
    access_method = "Groq hosted API" if args.hosted else "Ollama local"

    model_info = {
        "model_name": model_name,
        "model_version": model_ver,
        "access_method": access_method,
        "model_license": "Apache 2.0",
        "role_in_project": "Semantic risk analysis and migration code generation",
    }

    try:
        from depexit.capsule import generate_capsule
        json_path, md_path = generate_capsule(
            discovery=discovery,
            risks=risks,
            usages=usages,
            proposals=proposals,
            rehearsal=rehearsal,
            output_dir=output_dir,
            model_info=model_info,
        )
    except Exception as exc:
        console.print(
            Panel(
                f"[bold red]Stage 6 Capsule Generation Failed:[/bold red] {exc}",
                title="Stage 6 Error",
                border_style="red",
            )
        )
        json_path, md_path = None, None

    # Step 10 & 11: Completion summary and timing
    elapsed_time = time.time() - start_time

    summary_table = Table(title="GemmaMorph Execution Summary", header_style="bold green")
    summary_table.add_column("Pipeline Stage", style="bold")
    summary_table.add_column("Status", justify="center")
    summary_table.add_column("Details")

    discovery_deps = discovery.get("declared_deps") or discovery.get("dependencies") or []
    summary_table.add_row(
        "Stage 1: Discovery",
        "[green]OK[/green]" if discovery_deps else "[yellow]PARTIAL[/yellow]",
        f"{len(discovery_deps)} dependencies found",
    )
    summary_table.add_row(
        "Stage 2: Risk Scan",
        "[green]OK[/green]" if risks.get("risks") else "[yellow]PARTIAL[/yellow]",
        f"{len(risks.get('risks', {}))} components evaluated",
    )
    summary_table.add_row(
        "Stage 3: AST Usages",
        "[green]OK[/green]" if usages else "[yellow]PARTIAL[/yellow]",
        f"{sum(len(v) for v in usages.values())} callsites mapped",
    )
    summary_table.add_row(
        "Stage 4: Gemma Proposals",
        "[green]OK[/green]" if proposals else "[yellow]PARTIAL[/yellow]",
        f"{len(proposals)} migration plans generated",
    )
    summary_table.add_row(
        "Stage 5: Sandbox Rehearsal",
        "[yellow]SKIPPED[/yellow]" if args.skip_rehearsal else "[green]OK[/green]" if rehearsal.get("baseline") else "[red]FAILED[/red]",
        f"{len(rehearsal.get('per_proposal', []))} rehearsals executed",
    )
    summary_table.add_row(
        "Stage 6: Exit Capsule",
        "[green]OK[/green]" if (json_path and md_path) else "[red]FAILED[/red]",
        f"Capsules written to {output_dir.name}/",
    )

    console.print("")
    console.print(summary_table)

    status_message = (
        f"[bold green]Pipeline finished successfully in {elapsed_time:.2f} seconds![/bold green]\n\n"
        f"  [bold]JSON Capsule:[/bold]     [cyan]{json_path or 'N/A'}[/cyan]\n"
        f"  [bold]Markdown Capsule:[/bold] [cyan]{md_path or 'N/A'}[/cyan]"
    )
    console.print(Panel(status_message, title="Execution Complete", border_style="green"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
