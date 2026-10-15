"""
depexit.risk_scan - Stage 2: Dependency Risk Scanner.

This module evaluates dependency health and resilience risks using two evidence sources:
A) Metadata Risk (from PyPI API / offline cache):
   - Compares pinned version vs latest release (flags >2 major versions behind).
   - Identifies stale packages (last release >2 years ago).
   - Detects inactive or unmaintained development status classifiers.
   - Checks python_requires constraints against Python 3.12 / 3.13.
   - Caches PyPI responses locally in `_pypi_cache.json` for offline demonstration.

B) Observed Risk (from executing repo test suite):
   - Executes `pytest tests/ -v --tb=short` in a subprocess.
   - Extracts test counts: total, passed, failed, errors, warnings.
   - Attributes deprecation warnings to specific packages (e.g. pkg_resources -> setuptools, pydantic.v1 -> pydantic).
   - Attributes test failures/ImportErrors to specific packages (e.g. markupsafe -> Jinja2).

Designed for Hacktoberfest Hack Day Mumbai 2026 - GemmaMorph project.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from packaging.version import parse as parse_version
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .discover import KNOWN_IMPORT_TO_PKG, discover, normalize_name

# Default cache file path inside the depexit package directory
CACHE_FILENAME = "_pypi_cache.json"


def get_cache_file_path() -> Path:
    """Return the absolute path to the local PyPI cache file."""
    return Path(__file__).resolve().parent / CACHE_FILENAME


def load_pypi_cache() -> dict[str, Any]:
    """Load cached PyPI responses from disk if available."""
    cache_path = get_cache_file_path()
    if cache_path.is_file():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Warning: Failed to read cache {cache_path}: {e}", file=sys.stderr)
    return {}


def save_pypi_cache(cache_data: dict[str, Any]) -> None:
    """Persist PyPI responses to disk for offline access."""
    cache_path = get_cache_file_path()
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[!] Warning: Failed to write cache {cache_path}: {e}", file=sys.stderr)


def _compress_pypi_payload(raw_json: dict[str, Any]) -> dict[str, Any]:
    """Trim excessive release history while keeping all essential metadata and URLs."""
    info = raw_json.get("info", {})
    urls = raw_json.get("urls", [])
    latest_ver = info.get("version", "")

    # Keep only urls for the latest release to keep cache lightweight and fast
    compact: dict[str, Any] = {
        "info": info,
        "urls": urls[:3] if isinstance(urls, list) else [],
    }

    releases = raw_json.get("releases", {})
    if latest_ver and latest_ver in releases:
        compact["releases"] = {latest_ver: releases[latest_ver][:2]}
    return compact


def fetch_pypi_metadata(package_name: str, cache: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """
    Fetch package metadata from PyPI JSON API, falling back to local cache if offline.

    Returns:
        (metadata_dict, from_cache_boolean)
    """
    clean_name = normalize_name(package_name)
    url = f"https://pypi.org/pypi/{clean_name}/json"

    # Attempt live network request with 5s timeout
    try:
        resp = requests.get(
            url,
            timeout=5,
            headers={"User-Agent": "GemmaMorph-DepExit/1.0.0"},
        )
        if resp.status_code == 200:
            data = _compress_pypi_payload(resp.json())
            cache[clean_name] = data
            return data, False
    except (requests.RequestException, OSError):
        # Network unavailable or timed out; proceed to cache lookup
        pass

    # Check local cache for offline mode
    if clean_name in cache:
        return cache[clean_name], True

    # Also check if raw package_name exists in cache
    if package_name in cache:
        return cache[package_name], True

    return None, False


def _clean_version_str(ver_str: str) -> str:
    """Strip inequality operators (~, =, >, <, !, ^, space) from version string."""
    if not ver_str or ver_str == "*":
        return ""
    return re.sub(r"^[=><~^! ]+", "", ver_str).strip()


def _extract_release_date(pypi_data: dict[str, Any], latest_ver: str) -> str:
    """Extract release date ISO string for the latest version."""
    # 1. Check `urls` list (usually contains files for the latest release)
    urls = pypi_data.get("urls", [])
    if urls and isinstance(urls, list) and len(urls) > 0:
        upload_time = urls[0].get("upload_time_iso_8601") or urls[0].get("upload_time")
        if upload_time:
            return str(upload_time)

    # 2. Check `releases` dictionary for the latest version
    releases = pypi_data.get("releases", {})
    if latest_ver and latest_ver in releases and releases[latest_ver]:
        upload_time = releases[latest_ver][0].get("upload_time_iso_8601") or releases[latest_ver][0].get("upload_time")
        if upload_time:
            return str(upload_time)

    return ""


def evaluate_metadata_risk(
    package_name: str,
    pinned_version_spec: str,
    pypi_data: dict[str, Any] | None,
    from_cache: bool,
) -> dict[str, Any]:
    """
    Evaluate metadata risks for a single dependency according to PyPI metadata:
    1. Major version lag (>2 major versions behind).
    2. Stale package (last release >2 years ago).
    3. Development status contains 'Inactive' or no status classifier.
    4. python_requires excludes Python 3.12 or 3.13.
    """
    result: dict[str, Any] = {
        "package": package_name,
        "pinned_version": pinned_version_spec,
        "latest_version": "N/A",
        "last_release_date": "N/A",
        "development_status": "Unknown",
        "python_requires": "N/A",
        "homepage_url": "",
        "flags": [],
        "risk_score": "LOW",
        "from_cache": from_cache,
    }

    if not pypi_data:
        result["flags"].append("Unable to fetch PyPI metadata (offline and not cached)")
        result["risk_score"] = "UNKNOWN"
        return result

    info = pypi_data.get("info", {})
    latest_version = info.get("version", "")
    result["latest_version"] = latest_version

    homepage = (
        info.get("home_page")
        or (info.get("project_urls") or {}).get("Homepage")
        or (info.get("project_urls") or {}).get("Source")
        or ""
    )
    result["homepage_url"] = homepage

    python_requires = info.get("requires_python") or ""
    result["python_requires"] = python_requires

    # Extract Development Status classifier
    classifiers = info.get("classifiers", [])
    dev_status = ""
    for c in classifiers:
        if "Development Status" in c:
            dev_status = c.split("::")[-1].strip()
            break
    result["development_status"] = dev_status if dev_status else "Not specified"

    # Extract release date
    release_date_str = _extract_release_date(pypi_data, latest_version)
    result["last_release_date"] = release_date_str

    flags: list[str] = []

    # Flag 1: Pinned version is >2 major versions behind latest
    clean_pinned = _clean_version_str(pinned_version_spec)
    if clean_pinned and latest_version:
        try:
            pinned_ver_obj = parse_version(clean_pinned)
            latest_ver_obj = parse_version(latest_version)

            if hasattr(pinned_ver_obj, "major") and hasattr(latest_ver_obj, "major"):
                major_diff = latest_ver_obj.major - pinned_ver_obj.major
                if major_diff >= 2:
                    flags.append(
                        f"Major version lag: pinned {pinned_version_spec} is {major_diff} "
                        f"major versions behind latest ({latest_version})"
                    )
                elif major_diff == 1:
                    # Informative flag for major version transition
                    flags.append(
                        f"Major version update available: pinned {pinned_version_spec} vs latest {latest_version} (v{pinned_ver_obj.major} -> v{latest_ver_obj.major})"
                    )
        except InvalidVersion:
            pass

    # Flag 2: Last release >2 years ago
    if release_date_str:
        try:
            clean_date_str = release_date_str.replace("Z", "+00:00")
            rel_dt = datetime.fromisoformat(clean_date_str)
            now = datetime.now(timezone.utc)
            days_old = (now - rel_dt).days
            years_old = days_old / 365.25
            if years_old > 2.0:
                flags.append(f"Stale package: last release was {years_old:.1f} years ago ({release_date_str[:10]})")
        except Exception:
            pass

    # Flag 3: Development status contains 'Inactive' or no status
    if not dev_status:
        flags.append("Missing Development Status classifier on PyPI")
    elif any(term in dev_status.lower() for term in ["inactive", "unmaintained", "planning", "abandoned"]):
        flags.append(f"Development status indicates inactive/unmaintained: '{dev_status}'")

    # Flag 4: python_requires excludes 3.12 / 3.13
    if python_requires:
        try:
            spec = SpecifierSet(python_requires)
            v312 = Version("3.12")
            v313 = Version("3.13")
            if (v312 not in spec) or (v313 not in spec):
                flags.append(f"python_requires ('{python_requires}') excludes Python 3.12 and/or 3.13")
        except InvalidSpecifier:
            pass

    result["flags"] = flags

    # Calculate overall risk score
    is_inactive = any("inactive" in f.lower() for f in flags)
    has_major_lag = any("major version lag" in f.lower() for f in flags)
    is_stale = any("stale package" in f.lower() for f in flags)

    if is_inactive or (has_major_lag and is_stale) or len(flags) >= 2:
        result["risk_score"] = "HIGH"
    elif len(flags) == 1:
        result["risk_score"] = "MEDIUM"
    else:
        result["risk_score"] = "LOW"

    return result


def _attribute_warning(warning_text: str, location: str, declared_names: set[str]) -> str:
    """
    Attribute a deprecation or runtime warning to a specific package.
    e.g. 'pkg_resources is deprecated' -> setuptools
         'pydantic.v1' / '@validator' -> pydantic
         'markupsafe' -> jinja2
    """
    lower_text = warning_text.lower()
    lower_loc = location.lower()
    combined = f"{lower_loc} {lower_text}"

    # Specific rule-based attributions
    if "pkg_resources" in combined or "setuptools" in combined:
        return "setuptools"
    if "pydantic" in combined or "@validator" in combined or "field_validator" in combined or "configdict" in combined:
        return "pydantic"
    if "markupsafe" in combined or "soft_unicode" in combined:
        return "jinja2"
    if "urllib3" in combined:
        return "requests"
    if "appdirs" in combined:
        return "appdirs"
    if "dateutil" in combined:
        return "python-dateutil"
    if "chardet" in combined or "charset_normalizer" in combined:
        return "chardet"

    # Known import name to package mapping
    for mod_name, pkg in KNOWN_IMPORT_TO_PKG.items():
        if mod_name in combined:
            return pkg

    # Match declared dependencies
    for dep in declared_names:
        if dep in combined or dep.replace("-", "_") in combined:
            return dep

    return "unknown"


def _attribute_failure(error_text: str, test_name: str, declared_names: set[str]) -> str:
    """
    Attribute a test failure or exception to a specific dependency package.
    e.g. 'ImportError from markupsafe' -> jinja2
         'ModuleNotFoundError: No module named chardet' -> chardet
    """
    lower = f"{test_name} {error_text}".lower()

    # Match markupsafe -> jinja2
    if "markupsafe" in lower:
        return "jinja2"
    if "pkg_resources" in lower or "setuptools" in lower:
        return "setuptools"
    if "pydantic" in lower:
        return "pydantic"
    if "urllib3" in lower:
        return "requests"

    # Match ModuleNotFoundError or ImportError
    mod_match = re.search(r"(?:No module named|cannot import name .* from) ['\"]([A-Za-z0-9_]+)['\"]", error_text)
    if mod_match:
        mod = mod_match.group(1).lower()
        if mod in KNOWN_IMPORT_TO_PKG:
            return KNOWN_IMPORT_TO_PKG[mod]
        norm = normalize_name(mod)
        if norm in declared_names:
            return norm
        return norm

    # Match declared dependencies in error text
    for dep in declared_names:
        if dep in lower or dep.replace("-", "_") in lower:
            return dep

    return "unknown"


def run_observed_risk_tests(repo_path: Path, declared_names: set[str]) -> dict[str, Any]:
    """
    Stage 2 Part B: Run `pytest tests/ -v --tb=short` in target repo as a subprocess.
    Extract total tests, passed, failed, errors, warnings, and attribute warnings/failures.
    """
    repo_abs = repo_path.resolve()
    env = os.environ.copy()

    # Ensure repository root is on PYTHONPATH so target code can be imported
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{str(repo_abs)}{os.pathsep}{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = str(repo_abs)

    cmd = [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"]

    start_time = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_abs),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        duration = time.monotonic() - start_time
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        full_output = f"{stdout}\n{stderr}"
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as te:
        return {
            "test_summary": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "errors": 1,
                "warnings": 0,
                "exit_code": -1,
                "duration_seconds": 120.0,
            },
            "attributed_warnings": [],
            "attributed_failures": [
                {
                    "package": "unknown",
                    "test": "pytest execution",
                    "error": f"pytest timed out after 120s: {te}",
                }
            ],
            "raw_output": te.stdout or "Timeout",
        }
    except Exception as exc:
        return {
            "test_summary": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "errors": 1,
                "warnings": 0,
                "exit_code": -1,
                "duration_seconds": 0.0,
            },
            "attributed_warnings": [],
            "attributed_failures": [
                {
                    "package": "unknown",
                    "test": "pytest runner",
                    "error": f"Failed to run pytest: {exc}",
                }
            ],
            "raw_output": str(exc),
        }

    # 1. Parse summary line
    passed = int(m.group(1)) if (m := re.search(r"(\d+)\s+passed", full_output)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+)\s+failed", full_output)) else 0
    errors = int(m.group(1)) if (m := re.search(r"(\d+)\s+error", full_output)) else 0
    warnings = int(m.group(1)) if (m := re.search(r"(\d+)\s+warning", full_output)) else 0

    total = passed + failed + errors
    if total == 0:
        collected_m = re.search(r"collected\s+(\d+)\s+items", full_output)
        if collected_m:
            total = int(collected_m.group(1))

    # 2. Parse and attribute warnings
    attributed_warnings: list[dict[str, str]] = []
    if "=== warnings summary ===" in full_output:
        warning_section = full_output.split("=== warnings summary ===")[1]
        for delimiter in ["\n===", "\n-- Docs:"]:
            if delimiter in warning_section:
                warning_section = warning_section.split(delimiter)[0]

        # Break warnings into blocks: each starts with file:line
        raw_blocks: list[tuple[str, str]] = []
        cur_loc = ""
        cur_lines: list[str] = []

        for line in warning_section.splitlines():
            loc_match = re.match(r"^([A-Za-z]:[\\/][^\s:]+|[^\s:]+\.py):\d+", line.strip())
            if loc_match and not line.startswith("  "):
                if cur_loc and cur_lines:
                    raw_blocks.append((cur_loc, " ".join(cur_lines).strip()))
                    cur_lines = []
                cur_loc = line.strip()
            else:
                if cur_loc and line.strip():
                    cur_lines.append(line.strip())

        if cur_loc and cur_lines:
            raw_blocks.append((cur_loc, " ".join(cur_lines).strip()))

        for loc, text in raw_blocks:
            pkg = _attribute_warning(text, loc, declared_names)
            attributed_warnings.append({
                "package": pkg,
                "location": loc,
                "warning": text[:200] + ("..." if len(text) > 200 else ""),
            })

    # 3. Parse and attribute failures/errors
    attributed_failures: list[dict[str, str]] = []
    if failed > 0 or errors > 0:
        # Match failure headers like: ____ test_name ____
        failure_blocks = re.split(r"_{3,}\s+([^\n_]+)\s+_{3,}", full_output)
        if len(failure_blocks) > 1:
            for idx in range(1, len(failure_blocks), 2):
                test_name = failure_blocks[idx].strip()
                tb_body = failure_blocks[idx + 1] if idx + 1 < len(failure_blocks) else ""

                # Extract exception line starting with E
                err_lines = [l for l in tb_body.splitlines() if l.strip().startswith("E   ")]
                err_text = " ".join(err_lines).strip() if err_lines else tb_body[:200]

                pkg = _attribute_failure(err_text, test_name, declared_names)
                attributed_failures.append({
                    "package": pkg,
                    "test": test_name,
                    "error": err_text[:200],
                })
        else:
            # Collection error or general failure
            err_match = re.search(r"(?:E\s+.*|ImportError:.*|ModuleNotFoundError:.*)", full_output)
            err_text = err_match.group(0).strip() if err_match else full_output[-300:]
            pkg = _attribute_failure(err_text, "collection", declared_names)
            attributed_failures.append({
                "package": pkg,
                "test": "collection/setup",
                "error": err_text[:200],
            })

    return {
        "test_summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "warnings": warnings,
            "duration_seconds": round(duration, 2),
            "exit_code": exit_code,
        },
        "attributed_warnings": attributed_warnings,
        "attributed_failures": attributed_failures,
        "raw_output": full_output,
    }


def scan_risks(repo_path: Path | str, discovered: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Stage 2 Entrypoint: Run full risk scan (Metadata risks + Observed risks).

    Args:
        repo_path: Path to target repository.
        discovered: Optional pre-computed Stage 1 discovery dictionary.

    Returns:
        Structured dict with 'metadata_risks' and 'observed_risks'.
    """
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Target repository not found at: {root}")

    # If discovery result was not passed in, run Stage 1 discovery
    if discovered is None:
        discovered = discover(root)

    declared_deps = discovered.get("declared_deps", [])

    # Consolidate declared packages and identify preferred pinned versions
    unique_packages: dict[str, str] = {}
    for dep in declared_deps:
        name = dep["name"]
        ver = dep["pinned_version"]
        # Prefer specific pinned version (e.g. ==1.10.13) over unpinned or >=
        if name not in unique_packages:
            unique_packages[name] = ver
        elif unique_packages[name].startswith("*") and not ver.startswith("*"):
            unique_packages[name] = ver
        elif "==" in ver and "==" not in unique_packages[name]:
            unique_packages[name] = ver

    declared_names = set(unique_packages.keys())

    # --- Evidence A: PyPI Metadata Risks ---
    cache = load_pypi_cache()
    metadata_risks: list[dict[str, Any]] = []

    for pkg_name, pinned_ver in sorted(unique_packages.items()):
        pypi_data, from_cache = fetch_pypi_metadata(pkg_name, cache)
        risk_info = evaluate_metadata_risk(pkg_name, pinned_ver, pypi_data, from_cache)
        metadata_risks.append(risk_info)

    # Persist updated cache
    save_pypi_cache(cache)

    # --- Evidence B: Observed Test Risks ---
    observed_risks = run_observed_risk_tests(root, declared_names)

    return {
        "metadata_risks": metadata_risks,
        "observed_risks": observed_risks,
    }


# Convenience alias for alternate naming
risk_scan = scan_risks


def print_risk_summary(results: dict[str, Any], repo_path: Path) -> None:
    """Print rich formatted summary for Stage 2 Risk Scanner results."""
    console = Console()

    console.print()
    console.print(
        Panel.fit(
            f"[bold magenta]GemmaMorph Stage 2: Risk Scanner[/bold magenta]\n"
            f"[dim]Target Repository:[/dim] [white]{repo_path}[/white]",
            border_style="magenta",
        )
    )

    # 1. Metadata Risks Table
    meta_table = Table(title="[bold cyan]Evidence A: PyPI Metadata Risks[/bold cyan]", show_header=True)
    meta_table.add_column("Package", style="cyan", no_wrap=True)
    meta_table.add_column("Pinned Ver", style="white")
    meta_table.add_column("Latest Ver", style="green")
    meta_table.add_column("Last Release", style="dim")
    meta_table.add_column("Dev Status", style="dim")
    meta_table.add_column("Risk Level", justify="center")
    meta_table.add_column("Identified Risk Flags", style="yellow")

    for m in results.get("metadata_risks", []):
        score = m["risk_score"]
        if score == "HIGH":
            score_style = "[bold red]HIGH[/bold red]"
        elif score == "MEDIUM":
            score_style = "[bold yellow]MED[/bold yellow]"
        elif score == "LOW":
            score_style = "[bold green]LOW[/bold green]"
        else:
            score_style = "[dim]UNKNOWN[/dim]"

        flags_text = "\n".join(f"• {f}" for f in m["flags"]) if m["flags"] else "[dim]Healthy[/dim]"
        rel_date = m["last_release_date"][:10] if m["last_release_date"] != "N/A" else "N/A"

        meta_table.add_row(
            m["package"],
            m["pinned_version"],
            m["latest_version"],
            rel_date,
            m["development_status"][:20],
            score_style,
            flags_text,
        )

    console.print(meta_table)
    console.print()

    # 2. Observed Test Summary Panel
    obs = results.get("observed_risks", {})
    ts = obs.get("test_summary", {})

    status_str = "[bold green]PASSED[/bold green]" if ts.get("exit_code") == 0 else "[bold red]FAILED[/bold red]"
    summary_msg = (
        f"Result: {status_str} (Exit Code: {ts.get('exit_code')})\n"
        f"Total Tests: [bold]{ts.get('total')}[/bold] | "
        f"Passed: [green]{ts.get('passed')}[/green] | "
        f"Failed: [red]{ts.get('failed')}[/red] | "
        f"Errors: [red]{ts.get('errors')}[/red] | "
        f"Warnings: [yellow]{ts.get('warnings')}[/yellow] | "
        f"Duration: {ts.get('duration_seconds')}s"
    )
    console.print(
        Panel(
            summary_msg,
            title="[bold cyan]Evidence B: Observed Test Suite Execution[/bold cyan]",
            border_style="cyan",
        )
    )
    console.print()

    # 3. Attributed Warnings Table
    warnings = obs.get("attributed_warnings", [])
    if warnings:
        warn_table = Table(title="[bold yellow]Attributed Deprecation / Runtime Warnings[/bold yellow]")
        warn_table.add_column("Attributed Package", style="bold red", no_wrap=True)
        warn_table.add_column("Source Location", style="dim", no_wrap=True)
        warn_table.add_column("Warning Details", style="white")

        for w in warnings:
            warn_table.add_row(w["package"], w["location"], w["warning"])
        console.print(warn_table)
        console.print()

    # 4. Attributed Failures Table
    failures = obs.get("attributed_failures", [])
    if failures:
        fail_table = Table(title="[bold red]Attributed Test Failures[/bold red]")
        fail_table.add_column("Attributed Package", style="bold red", no_wrap=True)
        fail_table.add_column("Failed Test", style="yellow")
        fail_table.add_column("Error Message", style="white")

        for f in failures:
            fail_table.add_row(f["package"], f["test"], f["error"])
        console.print(fail_table)
        console.print()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent.parent / "LinkWatch"
    if not target.exists():
        target = Path.cwd()

    scan_output = scan_risks(target)
    print_risk_summary(scan_output, target)
