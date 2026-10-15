"""
Stage 3: AST-Based Dependency Usage Finder for GemmaMorph.

Scans the target repository using Python's standard `ast` library to identify
exact file locations, line numbers, import statements, API symbols, and call sites
for monitored dependencies.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from rich import box
from rich.console import Console
from rich.table import Table

console = Console()

# Canonical mapping from PyPI package names to top-level importable module names
KNOWN_PACKAGE_MODULE_MAP: dict[str, list[str]] = {
    "python-dateutil": ["dateutil"],
    "setuptools": ["setuptools", "pkg_resources", "distutils"],
    "pyyaml": ["yaml"],
    "jinja2": ["jinja2"],
    "beautifulsoup4": ["bs4"],
    "pillow": ["PIL"],
    "scikit-learn": ["sklearn"],
    "opencv-python": ["cv2"],
    "appdirs": ["appdirs"],
    "toml": ["toml"],
    "pydantic": ["pydantic"],
    "requests": ["requests"],
    "six": ["six"],
    "chardet": ["chardet"],
    "idna": ["idna"],
    "click": ["click"],
}

# Directories to exclude from AST scanning
IGNORED_DIRS: set[str] = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".tox",
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
    ".eggs",
}


def normalize_package_name(pkg: str) -> str:
    """Normalize a package name to canonical lowercase with hyphens."""
    return pkg.strip().lower().replace("_", "-")


def build_package_module_lookups(dependencies: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """
    Build bidirectional lookup tables between package names and module names.
    Returns:
        (pkg_to_modules, module_to_pkg)
    """
    pkg_to_modules: dict[str, list[str]] = {}
    module_to_pkg: dict[str, str] = {}

    for dep in dependencies:
        canonical_pkg = normalize_package_name(dep)
        modules = set()

        # Check known dictionary
        if canonical_pkg in KNOWN_PACKAGE_MODULE_MAP:
            modules.update(KNOWN_PACKAGE_MODULE_MAP[canonical_pkg])

        # Add default inferred module names
        clean_name = canonical_pkg.replace("-", "_")
        modules.add(clean_name)
        modules.add(canonical_pkg)

        module_list = sorted(list(modules))
        pkg_to_modules[dep] = module_list

        for mod in module_list:
            module_to_pkg[mod.lower()] = dep

    return pkg_to_modules, module_to_pkg


def extract_context_snippet(lines: list[str], lineno: int, window: int = 3) -> str:
    """
    Extract 3 lines above and 3 lines below the target line number.
    Returns a formatted multi-line string with line numbers and pointer.
    """
    if not lines:
        return ""
    start_idx = max(0, lineno - 1 - window)
    end_idx = min(len(lines), lineno + window)

    snippet_lines: list[str] = []
    for idx in range(start_idx, end_idx):
        current_no = idx + 1
        marker = "-> " if current_no == lineno else "   "
        line_content = lines[idx] if idx < len(lines) else ""
        snippet_lines.append(f"{marker}{current_no:4d} | {line_content}")

    return "\n".join(snippet_lines)


def get_full_statement_text(lines: list[str], node: ast.AST) -> str:
    """Extract full source text corresponding to an AST node spanning one or more lines."""
    start_line = getattr(node, "lineno", 1) - 1
    end_line = getattr(node, "end_lineno", getattr(node, "lineno", 1))
    if 0 <= start_line < len(lines):
        return "\n".join(lines[start_line:end_line]).strip()
    return ""


def get_attribute_chain(node: ast.AST) -> Optional[tuple[str, str]]:
    """
    Given an ast.Attribute or ast.Call, trace the root name and full attribute path.
    Example: `toml.load` -> root='toml', full_path='toml.load'
             `requests.Session.request` -> root='requests', full_path='requests.Session.request'
    """
    parts: list[str] = []
    curr = node
    while isinstance(curr, ast.Attribute):
        parts.append(curr.attr)
        curr = curr.value

    if isinstance(curr, ast.Name):
        parts.append(curr.id)
        parts.reverse()
        root = parts[0]
        full_path = ".".join(parts)
        return root, full_path

    return None


class RepoUsageVisitor(ast.NodeVisitor):
    """AST Visitor that tracks module imports and all subsequent usage sites."""

    def __init__(
        self,
        rel_path: str,
        source_lines: list[str],
        module_to_pkg: dict[str, str],
    ):
        self.rel_path = rel_path
        self.source_lines = source_lines
        self.module_to_pkg = module_to_pkg

        # Maps bound local name -> dict info
        # e.g., 'user_cache_dir' -> {'pkg': 'appdirs', 'api': 'user_cache_dir', 'stmt': '...'}
        self.bound_symbols: dict[str, dict[str, Any]] = {}

        # Maps bound module alias -> dict info
        # e.g., 'toml' -> {'pkg': 'toml', 'stmt': 'import toml'}
        self.bound_modules: dict[str, dict[str, Any]] = {}

        # Collected usages: pkg_name -> list[record]
        self.usages_by_pkg: dict[str, list[dict[str, Any]]] = {}

        # Dedup key set: (lineno, usage_type, api_names_str)
        self._seen: set[tuple[int, str, str]] = set()

    def _record_usage(
        self,
        pkg: str,
        lineno: int,
        usage_type: str,
        import_stmt: str,
        api_names: list[str],
        code_line: str,
    ) -> None:
        """Add a usage record with context snippet, avoiding duplicates."""
        api_key = ",".join(sorted(api_names))
        dedup_key = (lineno, usage_type, api_key)
        if dedup_key in self._seen:
            return
        self._seen.add(dedup_key)

        record = {
            "file_path": self.rel_path,
            "line_number": lineno,
            "usage_type": usage_type,
            "import_statement": import_stmt,
            "api_names": api_names,
            "code_line": code_line,
            "context_snippet": extract_context_snippet(self.source_lines, lineno),
        }

        self.usages_by_pkg.setdefault(pkg, []).append(record)

    def visit_Import(self, node: ast.Import) -> None:
        """Handle `import X` and `import X as Y` statements."""
        stmt_text = get_full_statement_text(self.source_lines, node)

        for alias in node.names:
            root_module = alias.name.split(".")[0].lower()
            if root_module in self.module_to_pkg:
                pkg = self.module_to_pkg[root_module]
                bound_name = alias.asname or alias.name
                self.bound_modules[bound_name] = {
                    "pkg": pkg,
                    "module": alias.name,
                    "import_statement": stmt_text,
                }

                code_line = (
                    self.source_lines[node.lineno - 1]
                    if node.lineno <= len(self.source_lines)
                    else stmt_text
                )

                self._record_usage(
                    pkg=pkg,
                    lineno=node.lineno,
                    usage_type="import",
                    import_stmt=stmt_text,
                    api_names=[alias.name],
                    code_line=code_line,
                )

        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Handle `from X import Y` and `from X.sub import Z` statements."""
        stmt_text = get_full_statement_text(self.source_lines, node)

        if node.module:
            root_module = node.module.split(".")[0].lower()
            if root_module in self.module_to_pkg:
                pkg = self.module_to_pkg[root_module]
                imported_names = [alias.name for alias in node.names]

                # Bind imported names for downstream call detection
                for alias in node.names:
                    bound_name = alias.asname or alias.name
                    self.bound_symbols[bound_name] = {
                        "pkg": pkg,
                        "full_api": f"{node.module}.{alias.name}",
                        "api_name": alias.name,
                        "import_statement": stmt_text,
                    }

                code_line = (
                    self.source_lines[node.lineno - 1]
                    if node.lineno <= len(self.source_lines)
                    else stmt_text
                )

                self._record_usage(
                    pkg=pkg,
                    lineno=node.lineno,
                    usage_type="import",
                    import_stmt=stmt_text,
                    api_names=imported_names,
                    code_line=code_line,
                )

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Handle `module.function()`, `module.attribute`, or chained calls."""
        chain = get_attribute_chain(node)
        if chain:
            root_name, full_path = chain
            # Check if root is a bound module import (e.g., toml.load, requests.Session)
            if root_name in self.bound_modules:
                mod_info = self.bound_modules[root_name]
                pkg = mod_info["pkg"]
                code_line = (
                    self.source_lines[node.lineno - 1]
                    if node.lineno <= len(self.source_lines)
                    else ""
                )
                self._record_usage(
                    pkg=pkg,
                    lineno=node.lineno,
                    usage_type="attribute_call",
                    import_stmt=mod_info["import_statement"],
                    api_names=[node.attr],
                    code_line=code_line,
                )
            # Direct check if root matches known module name directly
            elif root_name.lower() in self.module_to_pkg:
                pkg = self.module_to_pkg[root_name.lower()]
                code_line = (
                    self.source_lines[node.lineno - 1]
                    if node.lineno <= len(self.source_lines)
                    else ""
                )
                self._record_usage(
                    pkg=pkg,
                    lineno=node.lineno,
                    usage_type="attribute_call",
                    import_stmt=f"import {root_name}",
                    api_names=[node.attr],
                    code_line=code_line,
                )

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Handle direct calls like `user_cache_dir("linkwatch")` or `load_settings()`."""
        # Direct function call to an imported symbol: e.g. user_cache_dir(...)
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in self.bound_symbols:
                sym_info = self.bound_symbols[func_name]
                pkg = sym_info["pkg"]
                code_line = (
                    self.source_lines[node.lineno - 1]
                    if node.lineno <= len(self.source_lines)
                    else ""
                )
                self._record_usage(
                    pkg=pkg,
                    lineno=node.lineno,
                    usage_type="call",
                    import_stmt=sym_info["import_statement"],
                    api_names=[sym_info["api_name"]],
                    code_line=code_line,
                )

        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Handle class definitions inheriting from monitored base classes (e.g. BaseModel)."""
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in self.bound_symbols:
                sym_info = self.bound_symbols[base.id]
                pkg = sym_info["pkg"]
                code_line = (
                    self.source_lines[node.lineno - 1]
                    if node.lineno <= len(self.source_lines)
                    else ""
                )
                self._record_usage(
                    pkg=pkg,
                    lineno=node.lineno,
                    usage_type="base_class",
                    import_stmt=sym_info["import_statement"],
                    api_names=[sym_info["api_name"]],
                    code_line=code_line,
                )

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Handle decorator usages like @validator("name")."""
        for decorator in node.decorator_list:
            dec_name = None
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
                dec_name = decorator.func.id
            elif isinstance(decorator, ast.Name):
                dec_name = decorator.id

            if dec_name and dec_name in self.bound_symbols:
                sym_info = self.bound_symbols[dec_name]
                pkg = sym_info["pkg"]
                code_line = (
                    self.source_lines[decorator.lineno - 1]
                    if decorator.lineno <= len(self.source_lines)
                    else ""
                )
                self._record_usage(
                    pkg=pkg,
                    lineno=decorator.lineno,
                    usage_type="decorator",
                    import_stmt=sym_info["import_statement"],
                    api_names=[sym_info["api_name"]],
                    code_line=code_line,
                )

        self.generic_visit(node)


def find_usages(repo_path: Path, dependencies: list[str]) -> dict[str, list[dict[str, Any]]]:
    """
    Scan repo_path using AST to find all usages of specified dependencies.

    Args:
        repo_path: Absolute or relative Path to target repository root.
        dependencies: List of dependency names to search for (e.g. ['toml', 'appdirs', 'six']).

    Returns:
        Dictionary mapping package_name -> list of usage records.
    """
    repo_path = Path(repo_path).resolve()
    if not repo_path.is_dir():
        console.log(f"[bold red]Error:[/bold red] Target repo path not found: {repo_path}")
        return {dep: [] for dep in dependencies}

    pkg_to_modules, module_to_pkg = build_package_module_lookups(dependencies)

    # Initialize results dict
    results: dict[str, list[dict[str, Any]]] = {dep: [] for dep in dependencies}

    # Discover all .py files in repo, respecting ignore list
    py_files: list[Path] = []
    for p in repo_path.rglob("*.py"):
        # Check if any parent part is in IGNORED_DIRS
        parts = set(p.relative_to(repo_path).parts)
        if not parts.intersection(IGNORED_DIRS):
            py_files.append(p)

    # Parse and analyze each python file
    for py_file in py_files:
        rel_path = py_file.relative_to(repo_path).as_posix()
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            source_lines = source.splitlines()
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError) as err:
            console.log(f"[yellow]Skipping {rel_path} due to parse error: {err}[/yellow]")
            continue

        visitor = RepoUsageVisitor(rel_path, source_lines, module_to_pkg)
        visitor.visit(tree)

        # Merge findings
        for pkg, usages in visitor.usages_by_pkg.items():
            results.setdefault(pkg, []).extend(usages)

    # Print formatted rich table summary
    print_usage_summary_table(results)

    return results


def print_usage_summary_table(usages: dict[str, list[dict[str, Any]]]) -> None:
    """Print an attractive rich summary table of dependency usages."""
    table = Table(
        title="[bold green]GemmaMorph: Dependency Usage Analysis (Stage 3)[/bold green]",
        box=box.ROUNDED,
        header_style="bold magenta",
        title_style="bold green",
    )
    table.add_column("Dependency", style="bold cyan", min_width=15)
    table.add_column("Usages", justify="center", style="bold white", width=10)
    table.add_column("Files Involved", style="dim", min_width=25)
    table.add_column("Key APIs / Call Sites", style="yellow", min_width=30)
    table.add_column("Status", justify="center", width=22)

    for dep, records in usages.items():
        count = len(records)
        if count == 0:
            table.add_row(
                dep,
                "0",
                "-",
                "None detected in AST",
                "[bold yellow]Unused (Dead Import)[/bold yellow]",
            )
        else:
            files = sorted(list({r["file_path"] for r in records}))
            files_preview = ", ".join(files)
            if len(files_preview) > 35:
                files_preview = files_preview[:32] + "..."

            apis = set()
            for r in records:
                apis.update(r.get("api_names", []))
            apis_preview = ", ".join(sorted(list(apis)))
            if len(apis_preview) > 35:
                apis_preview = apis_preview[:32] + "..."

            table.add_row(
                dep,
                str(count),
                files_preview,
                apis_preview,
                "[bold green]Active Usage[/bold green]",
            )

    console.print(table)


if __name__ == "__main__":
    import sys

    # Quick test runner against LinkWatch or custom path
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent.parent / "LinkWatch"
    deps_to_test = [
        "toml",
        "appdirs",
        "pydantic",
        "Jinja2",
        "requests",
        "six",
        "chardet",
        "setuptools",
        "python-dateutil",
    ]
    console.print(f"[bold blue]Scanning repository:[/bold blue] {target}")
    findings = find_usages(target, deps_to_test)
    console.print(f"[bold green]Completed usage scan. Found usages for {len(findings)} dependencies.[/bold green]")
