"""
Stage 5: Exit Rehearsal Engine
================================
Runs live migration rehearsals in disposable, isolated sandbox clones
of the target repository to verify Gemma AI migration proposals before
certifying them in the Dependency Exit Capsule.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
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


def _handle_remove_readonly(func, path, exc_info):
    """Clear readonly bit and reattempt deletion on Windows."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _safe_rmtree(path: Path) -> None:
    """Safely delete a directory tree on Windows, ignoring lock hiccups."""
    if not path.exists():
        return
    try:
        shutil.rmtree(path, onerror=_handle_remove_readonly)
    except Exception:
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass


def _create_sandbox_copy(repo_path: Path, prefix: str = "rehearsal") -> Path:
    """
    Create a clean disposable sandbox clone of the target repository.
    Skips version control and cache artifacts for speed and Windows file safety.
    """
    timestamp = int(time.time() * 1000)
    sandbox_dir = Path(tempfile.gettempdir()) / f"_sandbox_{timestamp}_{prefix}"

    def _ignore_patterns(dir_path: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in {".git", ".pytest_cache", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache", "output"}:
                ignored.add(name)
            elif name.endswith((".pyc", ".pyo")):
                ignored.add(name)
        return ignored

    shutil.copytree(repo_path, sandbox_dir, ignore=_ignore_patterns, dirs_exist_ok=True)
    return sandbox_dir


def _parse_pytest_output(stdout: str, stderr: str, returncode: int) -> dict:
    """
    Parse verbose pytest output to extract passed, failed, and error test names
    along with runtime warnings.
    """
    combined = stdout + "\n" + stderr
    test_details: list[dict] = []
    passed_tests: list[str] = []
    failed_tests: list[str] = []
    skipped_tests: list[str] = []

    # Match test lines: e.g. tests/test_cache.py::test_cache_round_trip PASSED
    pattern = re.compile(
        r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)",
        re.MULTILINE
    )
    for match in pattern.finditer(combined):
        test_name = match.group(1)
        status = match.group(2)
        test_details.append({"name": test_name, "status": status})
        if status == "PASSED":
            passed_tests.append(test_name)
        elif status in ("FAILED", "ERROR"):
            failed_tests.append(test_name)
        elif status in ("SKIPPED", "XFAIL"):
            skipped_tests.append(test_name)

    total_passed = len(passed_tests)
    total_failed = len(failed_tests)

    # Fallback parsing for summary line if verbose lines were truncated
    summary_match = re.search(r"==+\s*(.*?)\s+in\s+[\d\.]+s\s*==+", combined)
    if summary_match:
        summary_text = summary_match.group(1)
        pass_m = re.search(r"(\d+)\s+passed", summary_text)
        fail_m = re.search(r"(\d+)\s+failed", summary_text)
        err_m = re.search(r"(\d+)\s+error", summary_text)
        if total_passed == 0 and pass_m:
            total_passed = int(pass_m.group(1))
        if total_failed == 0 and (fail_m or err_m):
            total_failed = (int(fail_m.group(1)) if fail_m else 0) + (int(err_m.group(1)) if err_m else 0)

    # Extract observed deprecation / user warnings from test run
    warnings: list[str] = []
    seen_warnings: set[str] = set()
    for line in combined.splitlines():
        line_clean = line.strip()
        if re.search(r"\b\w*(?:Warning|Deprecated)\w*:\s+", line_clean):
            if line_clean not in seen_warnings:
                seen_warnings.add(line_clean)
                warnings.append(line_clean)

    total = total_passed + total_failed + len(skipped_tests)
    if total == 0 and returncode != 0:
        total = 1
        total_failed = 1
        if not test_details:
            test_details.append({"name": "startup_or_collection", "status": "FAILED"})
            failed_tests.append("startup_or_collection")

    return {
        "total": total,
        "passed": total_passed,
        "failed": total_failed,
        "test_details": test_details,
        "passed_tests": passed_tests,
        "failed_tests": failed_tests,
        "skipped_tests": skipped_tests,
        "warnings": warnings,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _run_pytest_suite(sandbox_path: Path, timeout: int = 180) -> dict:
    """Run `python -m pytest tests/ -v --tb=short` in the sandbox."""
    tests_target = "tests/" if (sandbox_path / "tests").exists() else "."
    cmd = [sys.executable, "-m", "pytest", tests_target, "-v", "--tb=short"]

    try:
        res = subprocess.run(
            cmd,
            cwd=str(sandbox_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return _parse_pytest_output(res.stdout or "", res.stderr or "", res.returncode)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
        stderr = "Pytest execution timed out after 180 seconds."
        return _parse_pytest_output(stdout, stderr, -1)
    except Exception as exc:
        return _parse_pytest_output("", str(exc), -1)


def _apply_code_edits(sandbox_path: Path, code_edits: list[dict]) -> int:
    """
    Apply code edits generated by Gemma to the files in the sandbox.
    For each edit: reads target file, does content.replace(old_text, new_text),
    and writes back. If old_text is not found, logs a warning and skips.
    """
    applied_count = 0
    if not code_edits:
        return 0

    for idx, edit in enumerate(code_edits):
        if not isinstance(edit, dict):
            continue

        rel_file = edit.get("file") or edit.get("path") or edit.get("file_path") or edit.get("filename")
        if not rel_file:
            console.print(f"[yellow]  [!] Warning: Edit #{idx+1} missing target file path. Skipping.[/yellow]")
            continue

        target_file = sandbox_path / rel_file
        if not target_file.exists():
            # Check by basename in case relative path has slightly different prefix
            candidates = list(sandbox_path.rglob(Path(rel_file).name))
            if candidates:
                target_file = candidates[0]
            else:
                console.print(f"[yellow]  [!] Warning: Target file '{rel_file}' not found in sandbox. Skipping.[/yellow]")
                continue

        old_text = edit.get("old_text") or edit.get("old") or edit.get("search") or edit.get("find") or ""
        new_text = edit.get("new_text") or edit.get("new") or edit.get("replace") or edit.get("replacement") or ""

        if not old_text:
            console.print(f"[yellow]  [!] Warning: Edit #{idx+1} in '{rel_file}' has empty old_text. Skipping.[/yellow]")
            continue

        try:
            content = target_file.read_text(encoding="utf-8")
            if old_text not in content:
                # Handle Windows \r\n vs \n differences cleanly
                old_text_norm = old_text.replace("\r\n", "\n")
                content_norm = content.replace("\r\n", "\n")
                if old_text_norm in content_norm:
                    new_text_norm = new_text.replace("\r\n", "\n")
                    content_norm = content_norm.replace(old_text_norm, new_text_norm, 1)
                    target_file.write_text(content_norm, encoding="utf-8")
                    applied_count += 1
                    continue
                else:
                    console.print(f"[yellow]  [!] Warning: old_text not found in '{rel_file}'. Skipping edit.[/yellow]")
                    continue

            content = content.replace(old_text, new_text, 1)
            target_file.write_text(content, encoding="utf-8")
            applied_count += 1
        except Exception as exc:
            console.print(f"[yellow]  [!] Warning: Failed applying edit to '{rel_file}': {exc}[/yellow]")

    return applied_count


def _apply_requirements_edits(sandbox_path: Path, req_edits: Any) -> bool:
    """
    Update requirements.txt in the sandbox: remove retired dependencies
    and append modern replacement dependencies.
    """
    req_file = sandbox_path / "requirements.txt"
    if not req_file.exists():
        candidates = list(sandbox_path.glob("*requirements*.txt"))
        if candidates:
            req_file = candidates[0]
        else:
            req_file = sandbox_path / "requirements.txt"
            req_file.write_text("", encoding="utf-8")

    try:
        content = req_file.read_text(encoding="utf-8")
    except Exception:
        content = ""

    lines = content.splitlines()

    to_remove: list[str] = []
    to_add: list[str] = []

    if isinstance(req_edits, dict):
        to_remove = req_edits.get("remove") or req_edits.get("remove_lines") or []
        to_add = req_edits.get("add") or req_edits.get("add_lines") or []
    elif isinstance(req_edits, list):
        for item in req_edits:
            if isinstance(item, str):
                if item.startswith("-"):
                    to_remove.append(item.lstrip("- ").strip())
                elif item.startswith("+"):
                    to_add.append(item.lstrip("+ ").strip())
                else:
                    to_add.append(item.strip())
            elif isinstance(item, dict):
                act = item.get("action", "").lower()
                pkg = item.get("package") or item.get("line") or item.get("name") or ""
                if act == "remove":
                    to_remove.append(pkg)
                elif act == "add":
                    to_add.append(pkg)

    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        pkg_base = re.split(r"[=<>!~]", stripped)[0].strip().lower()
        should_remove = False
        for rem in to_remove:
            rem_clean = rem.strip()
            rem_base = re.split(r"[=<>!~]", rem_clean)[0].strip().lower()
            if pkg_base == rem_base or rem_clean == stripped or rem_clean in stripped:
                should_remove = True
                break
        if not should_remove:
            new_lines.append(line)

    for add_item in to_add:
        add_item_clean = add_item.strip()
        if add_item_clean and add_item_clean not in new_lines:
            new_lines.append(add_item_clean)

    try:
        req_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True
    except Exception as exc:
        console.print(f"[yellow]  [!] Warning: Failed updating requirements.txt: {exc}[/yellow]")
        return False


def _install_sandbox_requirements(sandbox_path: Path, timeout: int = 180) -> bool:
    """
    Run `python -m pip install -r requirements.txt --quiet` inside the sandbox.
    """
    req_file = sandbox_path / "requirements.txt"
    if not req_file.exists():
        return True

    cmd = [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--quiet"]
    try:
        res = subprocess.run(
            cmd,
            cwd=str(sandbox_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode != 0:
            console.print(f"[red]  [x] pip install failed (exit {res.returncode}):[/red] {res.stderr[:240]}")
            return False
        return True
    except Exception as exc:
        console.print(f"[red]  [x] pip install execution error:[/red] {exc}")
        return False


def run_rehearsal(repo_path: Path, proposals: list[dict], keep_sandbox: bool = False) -> dict:
    """
    Run live migration rehearsals in isolated disposable sandbox copies.

    Parameters:
        repo_path: Path to the target repository.
        proposals: List of migration proposals from Stage 4 (Gemma).
        keep_sandbox: If True, keep temp sandboxes on disk for inspection.

    Returns:
        dict: {
            "baseline": {total, passed, failed, test_details, ...},
            "per_proposal": [{dep_name, applied_edits, install_ok, total, passed, failed, newly_failing, newly_passing, warnings_diff}],
            "combined": {applied_proposals, total, passed, failed, newly_failing}
        }
    """
    repo_path = Path(repo_path).resolve()
    if not repo_path.exists():
        raise FileNotFoundError(f"Target repository path '{repo_path}' does not exist.")

    sandboxes_to_clean: list[Path] = []

    console.print("\n[bold cyan]─── Stage 5: Exit Rehearsal Engine ───[/bold cyan]")
    console.print(f"[dim]Cloning disposable test sandboxes for {len(proposals)} proposal(s)...[/dim]")

    try:
        # Step 1 & 2: Baseline run in sandbox
        console.print("[bold yellow]Running Baseline Test Suite...[/bold yellow]")
        baseline_sandbox = _create_sandbox_copy(repo_path, prefix="baseline")
        sandboxes_to_clean.append(baseline_sandbox)

        baseline_res = _run_pytest_suite(baseline_sandbox)
        baseline = {
            "total": baseline_res["total"],
            "passed": baseline_res["passed"],
            "failed": baseline_res["failed"],
            "test_details": baseline_res["test_details"],
            "passed_tests": baseline_res["passed_tests"],
            "failed_tests": baseline_res["failed_tests"],
            "warnings": baseline_res["warnings"],
            "returncode": baseline_res["returncode"],
        }

        console.print(
            f"  [green]Baseline Tests Completed:[/green] "
            f"[bold green]{baseline['passed']}[/bold green] passed, "
            f"[bold red]{baseline['failed']}[/bold red] failed, "
            f"[bold yellow]{len(baseline['warnings'])}[/bold yellow] warnings "
            f"({baseline['total']} total)"
        )

        per_proposal: list[dict] = []
        eligible_for_combined: list[dict] = []

        # Step 3: Rehearse each proposal individually
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Rehearsing proposals...", total=len(proposals))

            for prop in proposals:
                dep_name = (
                    prop.get("dep_name")
                    or prop.get("name")
                    or prop.get("dependency")
                    or "unknown"
                )
                progress.update(task, description=f"[cyan]Rehearsing '{dep_name}' migration...")

                prop_sandbox = _create_sandbox_copy(repo_path, prefix=f"prop_{dep_name}")
                sandboxes_to_clean.append(prop_sandbox)

                code_edits = prop.get("code_edits") or []
                req_edits = prop.get("requirements_edits") or {}

                applied_edits = _apply_code_edits(prop_sandbox, code_edits)
                _apply_requirements_edits(prop_sandbox, req_edits)

                install_ok = _install_sandbox_requirements(prop_sandbox)

                if not install_ok:
                    res_entry = {
                        "dep_name": dep_name,
                        "applied_edits": applied_edits,
                        "install_ok": False,
                        "install_failed": True,
                        "total": baseline["total"],
                        "passed": 0,
                        "failed": baseline["total"],
                        "newly_failing": [],
                        "newly_passing": [],
                        "warnings_diff": {
                            "added": [],
                            "removed": [],
                            "baseline_count": len(baseline["warnings"]),
                            "post_count": 0,
                        },
                        "status": "INSTALL_FAILED",
                        "test_details": [],
                    }
                    per_proposal.append(res_entry)
                    progress.advance(task)
                    continue

                # Run post-migration tests
                prop_res = _run_pytest_suite(prop_sandbox)

                # Compare with baseline
                newly_failing = [
                    t for t in prop_res["failed_tests"]
                    if t in baseline["passed_tests"]
                ]
                newly_passing = [
                    t for t in prop_res["passed_tests"]
                    if t in baseline["failed_tests"]
                ]
                warnings_added = [
                    w for w in prop_res["warnings"]
                    if w not in baseline["warnings"]
                ]
                warnings_removed = [
                    w for w in baseline["warnings"]
                    if w not in prop_res["warnings"]
                ]

                status = "SUCCESS" if len(newly_failing) == 0 else "REGRESSION"

                res_entry = {
                    "dep_name": dep_name,
                    "applied_edits": applied_edits,
                    "install_ok": True,
                    "install_failed": False,
                    "total": prop_res["total"],
                    "passed": prop_res["passed"],
                    "failed": prop_res["failed"],
                    "newly_failing": newly_failing,
                    "newly_passing": newly_passing,
                    "warnings_diff": {
                        "added": warnings_added,
                        "removed": warnings_removed,
                        "baseline_count": len(baseline["warnings"]),
                        "post_count": len(prop_res["warnings"]),
                    },
                    "status": status,
                    "test_details": prop_res["test_details"],
                }
                per_proposal.append(res_entry)

                if status == "SUCCESS":
                    eligible_for_combined.append(prop)

                progress.advance(task)

        # Step 4: Combined rehearsal
        console.print("\n[bold yellow]Running Combined Exit Rehearsal...[/bold yellow]")
        if eligible_for_combined:
            comb_names = [
                p.get("dep_name") or p.get("name") or p.get("dependency")
                for p in eligible_for_combined
            ]
            console.print(f"  [dim]Applying all passing proposals together: {', '.join(comb_names)}[/dim]")

            comb_sandbox = _create_sandbox_copy(repo_path, prefix="combined")
            sandboxes_to_clean.append(comb_sandbox)

            total_comb_edits = 0
            for prop in eligible_for_combined:
                total_comb_edits += _apply_code_edits(comb_sandbox, prop.get("code_edits") or [])
                _apply_requirements_edits(comb_sandbox, prop.get("requirements_edits") or {})

            comb_install_ok = _install_sandbox_requirements(comb_sandbox)
            if comb_install_ok:
                comb_res = _run_pytest_suite(comb_sandbox)
                comb_newly_failing = [
                    t for t in comb_res["failed_tests"]
                    if t in baseline["passed_tests"]
                ]
                comb_status = "SUCCESS" if len(comb_newly_failing) == 0 else "REGRESSION"
                combined = {
                    "applied_proposals": comb_names,
                    "total": comb_res["total"],
                    "passed": comb_res["passed"],
                    "failed": comb_res["failed"],
                    "newly_failing": comb_newly_failing,
                    "status": comb_status,
                }
            else:
                combined = {
                    "applied_proposals": comb_names,
                    "total": baseline["total"],
                    "passed": 0,
                    "failed": baseline["total"],
                    "newly_failing": ["pip_install_failure"],
                    "status": "INSTALL_FAILED",
                }
        else:
            combined = {
                "applied_proposals": [],
                "total": baseline["total"],
                "passed": baseline["passed"],
                "failed": baseline["failed"],
                "newly_failing": [],
                "status": "NO_ELIGIBLE_PROPOSALS",
            }

        # Render summary table in console
        _print_rehearsal_table(baseline, per_proposal, combined)

        return {
            "baseline": baseline,
            "per_proposal": per_proposal,
            "combined": combined,
        }

    finally:
        # Step 5: Sandbox Cleanup
        if keep_sandbox:
            console.print("\n[yellow][*] Flag --keep-sandbox enabled. Retaining sandboxes:[/yellow]")
            for s in sandboxes_to_clean:
                console.print(f"  [dim]- {s}[/dim]")
        else:
            for s in sandboxes_to_clean:
                _safe_rmtree(s)


def _print_rehearsal_table(baseline: dict, per_proposal: list[dict], combined: dict) -> None:
    """Format and print an executive rehearsal summary table to the console."""
    table = Table(title="Live Rehearsal Test Matrix", header_style="bold magenta")
    table.add_column("Dependency", style="bold")
    table.add_column("Applied Edits", justify="center")
    table.add_column("Pip Install", justify="center")
    table.add_column("Passed", justify="right", style="green")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("New Regressions", justify="center")
    table.add_column("Verdict", justify="center")

    for p in per_proposal:
        install_str = "[green]OK[/green]" if p.get("install_ok") else "[red]FAIL[/red]"
        new_fail_count = len(p.get("newly_failing", []))
        regress_str = f"[red]{new_fail_count}[/red]" if new_fail_count > 0 else "[green]0[/green]"

        if p.get("status") == "SUCCESS":
            verdict = "[bold green][VERIFIED][/bold green]"
        elif p.get("status") == "INSTALL_FAILED":
            verdict = "[bold red][INSTALL FAIL][/bold red]"
        else:
            verdict = "[bold red][REFUTED][/bold red]"

        table.add_row(
            p.get("dep_name", "unknown"),
            str(p.get("applied_edits", 0)),
            install_str,
            str(p.get("passed", 0)),
            str(p.get("failed", 0)),
            regress_str,
            verdict,
        )

    console.print(table)

    # Combined summary
    comb_status = combined.get("status", "N/A")
    comb_color = "green" if comb_status == "SUCCESS" else "yellow" if comb_status == "NO_ELIGIBLE_PROPOSALS" else "red"
    console.print(
        Panel(
            f"Applied Proposals : [bold]{', '.join(combined.get('applied_proposals', [])) or 'None'}[/bold]\n"
            f"Test Suite Outcome: [bold green]{combined.get('passed', 0)} passed[/bold green], "
            f"[bold red]{combined.get('failed', 0)} failed[/bold red] "
            f"({len(combined.get('newly_failing', []))} new failures)\n"
            f"Combined Status   : [{comb_color} bold]{comb_status}[/{comb_color} bold]",
            title="Combined Rehearsal Verdict",
            border_style=comb_color,
        )
    )
