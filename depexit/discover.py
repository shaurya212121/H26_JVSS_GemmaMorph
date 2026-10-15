"""
depexit.discover - Stage 1: Dependency Discovery Engine.

This module inspects a target Python repository to discover:
1. Declared dependencies from requirements*.txt and pyproject.toml with pinned versions.
2. Version conflicts across different manifest files (e.g. pydantic==1.10.13 vs pydantic>=2).
3. Undeclared direct imports - packages imported in Python files but never declared in manifests.

Designed for Hacktoberfest Hack Day Mumbai 2026 - GemmaMorph project.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Known package-to-import name mappings
KNOWN_PKG_TO_IMPORT: dict[str, str] = {
    "python-dateutil": "dateutil",
    "pillow": "PIL",
    "pyyaml": "yaml",
    "beautifulsoup4": "bs4",
    "scikit-learn": "sklearn",
    "setuptools": "pkg_resources",
}

# Inverse mapping: import module name -> canonical package name
KNOWN_IMPORT_TO_PKG: dict[str, str] = {
    "dateutil": "python-dateutil",
    "pil": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "pkg_resources": "setuptools",
}


def normalize_name(name: str) -> str:
    """
    Normalize package name per PEP 503 / PEP 625 standards:
    Convert to lowercase and replace underscores and dots with hyphens.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _strip_inline_comment(line: str) -> str:
    """
    Safely strip trailing inline comment from a line in a requirements file.
    Does not strip `#` if inside quotes or URL fragments.
    """
    # If the line contains a hash preceded by whitespace, split it
    parts = re.split(r"\s+#", line, maxsplit=1)
    return parts[0].strip()


def parse_requirements_file(
    file_path: Path, repo_root: Path, visited: set[Path] | None = None
) -> list[dict[str, str]]:
    """
    Parse a requirements*.txt file line by line.
    Handles comments, empty lines, recursive `-r` includes, and extras.

    Returns a list of dicts: {"name": str, "pinned_version": str, "source_file": str}
    """
    if visited is None:
        visited = set()

    resolved_path = file_path.resolve()
    if resolved_path in visited or not resolved_path.is_file():
        return []
    visited.add(resolved_path)

    results: list[dict[str, str]] = []
    rel_source = (
        resolved_path.relative_to(repo_root.resolve()).as_posix()
        if repo_root.resolve() in resolved_path.parents
        else resolved_path.name
    )

    try:
        content = resolved_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[!] Warning: Unable to read {resolved_path}: {e}", file=sys.stderr)
        return []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        line = _strip_inline_comment(line)
        if not line:
            continue

        # Handle nested includes: -r other.txt or --requirement other.txt
        if line.startswith(("-r ", "--requirement ")):
            include_arg = line.split(maxsplit=1)[1].strip()
            included_file = (resolved_path.parent / include_arg).resolve()
            results.extend(parse_requirements_file(included_file, repo_root, visited))
            continue

        # Skip pip CLI options (e.g., -i, --index-url, -f, --find-links, --trusted-host)
        if line.startswith(("-i ", "--index-url", "-f ", "--find-links", "--extra-index-url", "--trusted-host")):
            continue

        # Parse requirement using packaging.requirements.Requirement
        try:
            req = Requirement(line)
            norm_name = normalize_name(req.name)
            spec_str = str(req.specifier) if req.specifier else "*"
            results.append({
                "name": norm_name,
                "pinned_version": spec_str,
                "source_file": rel_source,
            })
        except InvalidRequirement:
            # Fallback simple regex parsing for non-standard lines
            match = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*([=><~^!].*)?$", line)
            if match:
                norm_name = normalize_name(match.group(1))
                spec_str = match.group(2).strip() if match.group(2) else "*"
                results.append({
                    "name": norm_name,
                    "pinned_version": spec_str,
                    "source_file": rel_source,
                })

    return results


def parse_pyproject_toml(file_path: Path, repo_root: Path) -> list[dict[str, str]]:
    """
    Parse dependencies from pyproject.toml using standard library `tomllib`.
    Extracts [project].dependencies and [project.optional-dependencies].
    """
    resolved_path = file_path.resolve()
    if not resolved_path.is_file():
        return []

    rel_source = (
        resolved_path.relative_to(repo_root.resolve()).as_posix()
        if repo_root.resolve() in resolved_path.parents
        else resolved_path.name
    )

    try:
        with open(resolved_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        print(f"[!] Warning: Could not parse {resolved_path}: {e}", file=sys.stderr)
        return []

    results: list[dict[str, str]] = []

    # Parse [project].dependencies
    project_table = data.get("project", {})
    deps = project_table.get("dependencies", [])
    for dep_str in deps:
        if not isinstance(dep_str, str) or not dep_str.strip():
            continue
        try:
            req = Requirement(dep_str.strip())
            norm_name = normalize_name(req.name)
            spec_str = str(req.specifier) if req.specifier else "*"
            results.append({
                "name": norm_name,
                "pinned_version": spec_str,
                "source_file": rel_source,
            })
        except InvalidRequirement:
            match = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*([=><~^!].*)?$", dep_str.strip())
            if match:
                norm_name = normalize_name(match.group(1))
                spec_str = match.group(2).strip() if match.group(2) else "*"
                results.append({
                    "name": norm_name,
                    "pinned_version": spec_str,
                    "source_file": rel_source,
                })

    # Also parse [project.optional-dependencies] if present
    opt_deps = project_table.get("optional-dependencies", {})
    for group_name, opt_list in opt_deps.items():
        if not isinstance(opt_list, list):
            continue
        for dep_str in opt_list:
            if not isinstance(dep_str, str) or not dep_str.strip():
                continue
            try:
                req = Requirement(dep_str.strip())
                norm_name = normalize_name(req.name)
                spec_str = str(req.specifier) if req.specifier else "*"
                results.append({
                    "name": norm_name,
                    "pinned_version": spec_str,
                    "source_file": f"{rel_source} [{group_name}]",
                })
            except InvalidRequirement:
                pass

    return results


def detect_local_modules(repo_path: Path) -> set[str]:
    """
    Detect local package/module names inside the repository to avoid flagging
    internal project modules as undeclared external dependencies.
    """
    local_names: set[str] = set()

    # Search top-level directories and files
    for item in repo_path.iterdir():
        if item.name.startswith("."):
            continue
        if item.is_dir() and (item / "__init__.py").exists():
            local_names.add(item.name.lower())
        elif item.is_file() and item.suffix == ".py":
            local_names.add(item.stem.lower())

    # Check under `src/` layout if present
    src_dir = repo_path / "src"
    if src_dir.is_dir():
        for item in src_dir.iterdir():
            if item.name.startswith("."):
                continue
            if item.is_dir() and (item / "__init__.py").exists():
                local_names.add(item.name.lower())
            elif item.is_file() and item.suffix == ".py":
                local_names.add(item.stem.lower())

    # Check pyproject.toml [project].name
    pyproject_file = repo_path / "pyproject.toml"
    if pyproject_file.is_file():
        try:
            with open(pyproject_file, "rb") as f:
                data = tomllib.load(f)
                proj_name = data.get("project", {}).get("name")
                if proj_name:
                    local_names.add(normalize_name(proj_name))
                    local_names.add(proj_name.lower().replace("-", "_"))
        except Exception:
            pass

    # Standard common local folders that are not third-party packages
    local_names.update({"tests", "test", "docs"})
    return local_names


def scan_ast_imports(repo_path: Path, local_modules: set[str]) -> dict[str, list[str]]:
    """
    Walk all .py files in repo_path and use AST to extract top-level import names.
    Filters out Python standard library modules and internal project modules.
    Maps import names to package names using KNOWN_IMPORT_TO_PKG.

    Returns dict mapping package name -> list of relative file paths where imported.
    """
    imported_packages: dict[str, list[str]] = {}
    stdlib_modules = frozenset(sys.stdlib_module_names)

    # Walk all .py files excluding hidden/virtual environment directories
    skip_dirs = {".git", ".venv", "venv", ".env", "env", "__pycache__", ".pytest_cache", ".eggs", "build", "dist"}

    for py_file in repo_path.rglob("*.py"):
        if any(part in skip_dirs or part.startswith(".") for part in py_file.parts):
            continue

        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(py_file))
        except Exception:
            continue

        rel_path = py_file.relative_to(repo_path).as_posix()

        for node in ast.walk(tree):
            raw_modules: list[str] = []

            if isinstance(node, ast.Import):
                for alias in node.names:
                    raw_modules.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                # node.level == 0 means absolute import, node.level > 0 means relative
                if node.level == 0 and node.module:
                    raw_modules.append(node.module.split(".")[0])

            for mod in raw_modules:
                mod_lower = mod.lower()
                # 1. Skip standard library modules
                if mod in stdlib_modules or mod_lower in stdlib_modules:
                    continue
                # 2. Skip local repository modules
                if mod_lower in local_modules or mod.replace("_", "-").lower() in local_modules:
                    continue

                # 3. Map module name to package name
                if mod_lower in KNOWN_IMPORT_TO_PKG:
                    pkg_name = KNOWN_IMPORT_TO_PKG[mod_lower]
                else:
                    pkg_name = normalize_name(mod)

                if pkg_name not in imported_packages:
                    imported_packages[pkg_name] = []
                if rel_path not in imported_packages[pkg_name]:
                    imported_packages[pkg_name].append(rel_path)

    return imported_packages


def find_version_conflicts(declared_deps: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Compare declared dependencies across different manifest sources.
    Identifies version discrepancies (e.g. pydantic==1.10.13 vs pydantic>=2).

    Returns list of dicts: {name, version_a, source_a, version_b, source_b}
    """
    by_name: dict[str, list[dict[str, str]]] = {}
    for dep in declared_deps:
        by_name.setdefault(dep["name"], []).append(dep)

    conflicts: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, tuple[str, str]]] = set()

    for name, entries in by_name.items():
        if len(entries) < 2:
            continue

        # Check all unique pairs of entries
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                entry_a = entries[i]
                entry_b = entries[j]

                # Different source files with different version requirements
                if (
                    entry_a["source_file"] != entry_b["source_file"]
                    and entry_a["pinned_version"] != entry_b["pinned_version"]
                ):
                    source_pair = tuple(sorted([entry_a["source_file"], entry_b["source_file"]]))
                    pair_key = (name, source_pair)
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        conflicts.append({
                            "name": name,
                            "version_a": entry_a["pinned_version"],
                            "source_a": entry_a["source_file"],
                            "version_b": entry_b["pinned_version"],
                            "source_b": entry_b["source_file"],
                        })

    return conflicts


def discover(repo_path: Path | str) -> dict[str, Any]:
    """
    Stage 1 Entrypoint: Discover dependencies, conflicts, and undeclared imports.

    Args:
        repo_path: Root path of the target Python repository.

    Returns:
        A dict containing:
            - declared_deps: list of {name, pinned_version, source_file}
            - conflicts: list of {name, version_a, source_a, version_b, source_b}
            - undeclared_imports: list of {name, imported_in}
    """
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Target repository not found at: {root}")

    # 1. Discover all declared dependencies from requirements*.txt
    declared_deps: list[dict[str, str]] = []
    for req_file in sorted(root.glob("requirements*.txt")):
        declared_deps.extend(parse_requirements_file(req_file, root))

    # Also check pyproject.toml
    pyproject_file = root / "pyproject.toml"
    if pyproject_file.is_file():
        declared_deps.extend(parse_pyproject_toml(pyproject_file, root))

    # 2. Identify version conflicts across sources
    conflicts = find_version_conflicts(declared_deps)

    # 3. Detect internal/local modules to prevent false undeclared alerts
    local_modules = detect_local_modules(root)

    # 4. AST-walk all .py files to extract top-level third-party imports
    imported_packages = scan_ast_imports(root, local_modules)

    # Build declared package names set (supporting both package names and import names)
    declared_pkg_names = {dep["name"] for dep in declared_deps}
    for dep in declared_deps:
        if dep["name"] in KNOWN_PKG_TO_IMPORT:
            declared_pkg_names.add(normalize_name(KNOWN_PKG_TO_IMPORT[dep["name"]]))

    # 5. Identify undeclared direct imports
    undeclared_imports: list[dict[str, Any]] = []
    for pkg_name, files in sorted(imported_packages.items()):
        if pkg_name not in declared_pkg_names:
            undeclared_imports.append({
                "name": pkg_name,
                "imported_in": sorted(files),
            })

    return {
        "declared_deps": declared_deps,
        "conflicts": conflicts,
        "undeclared_imports": undeclared_imports,
    }


def print_discovery_summary(result: dict[str, Any], repo_path: Path) -> None:
    """Print formatted rich tables for discovery results."""
    console = Console()

    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]GemmaMorph Stage 1: Dependency Discovery[/bold cyan]\n"
            f"[dim]Target Repository:[/dim] [white]{repo_path}[/white]",
            border_style="cyan",
        )
    )

    # Table 1: Declared Dependencies
    dep_table = Table(title="[bold green]Declared Dependencies[/bold green]", show_header=True)
    dep_table.add_column("Package Name", style="cyan", no_wrap=True)
    dep_table.add_column("Pinned Version", style="magenta")
    dep_table.add_column("Source Manifest", style="yellow")

    for dep in result["declared_deps"]:
        dep_table.add_row(dep["name"], dep["pinned_version"], dep["source_file"])
    console.print(dep_table)
    console.print()

    # Table 2: Version Conflicts
    conflict_table = Table(title="[bold red]Version Conflicts[/bold red]", show_header=True)
    conflict_table.add_column("Package Name", style="bold red", no_wrap=True)
    conflict_table.add_column("Version A", style="cyan")
    conflict_table.add_column("Source A", style="yellow")
    conflict_table.add_column("Version B", style="magenta")
    conflict_table.add_column("Source B", style="yellow")

    if result["conflicts"]:
        for c in result["conflicts"]:
            conflict_table.add_row(
                c["name"],
                c["version_a"],
                c["source_a"],
                c["version_b"],
                c["source_b"],
            )
    else:
        conflict_table.add_row("None", "-", "-", "-", "-")
    console.print(conflict_table)
    console.print()

    # Table 3: Undeclared Direct Imports
    undec_table = Table(title="[bold yellow]Undeclared Direct Imports[/bold yellow]", show_header=True)
    undec_table.add_column("Package Name", style="bold yellow", no_wrap=True)
    undec_table.add_column("Imported In File(s)", style="white")

    if result["undeclared_imports"]:
        for u in result["undeclared_imports"]:
            undec_table.add_row(u["name"], ", ".join(u["imported_in"]))
    else:
        undec_table.add_row("None", "-")
    console.print(undec_table)
    console.print()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent.parent / "LinkWatch"
    if not target.exists():
        target = Path.cwd()

    discovery_result = discover(target)
    print_discovery_summary(discovery_result, target)
