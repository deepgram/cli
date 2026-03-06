#!/usr/bin/env python3
"""Generate README.md files for sub-packages and update root README sections.

Usage:
    python scripts/generate_readmes.py              # Generate all READMEs
    python scripts/generate_readmes.py --dry-run    # Preview without writing
    python scripts/generate_readmes.py --check      # Exit non-zero if stale
    python scripts/generate_readmes.py --package deepctl-core  # Single package
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        print(
            "Python 3.11+ required (tomllib), "
            "or install tomli: pip install tomli"
        )
        sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = ROOT / "packages"

# Skip packages with comprehensive manual READMEs
SKIP_PACKAGES: set[str] = set()

# Internal package prefixes to exclude from dependency lists
INTERNAL_PREFIXES = ("deepctl-core", "deepctl-cmd-", "deepctl-shared-")


def load_pyproject(package_dir: Path) -> dict:
    """Load and return parsed pyproject.toml."""
    with open(package_dir / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


def classify_package(name: str, data: dict) -> str:
    """Classify a package as 'command', 'debug-subcommand', or 'core'."""
    if re.match(r"^deepctl-cmd-debug-.+$", name):
        return "debug-subcommand"
    if name.startswith("deepctl-cmd-"):
        return "command"
    eps = data.get("project", {}).get("entry-points", {})
    if eps.get("deepctl.plugins") or eps.get("deepctl.commands"):
        return "command"
    return "core"


def get_external_deps(deps: list[str]) -> list[str]:
    """Filter out internal deepctl-* dependencies."""
    external = []
    for dep in deps:
        dep_name = re.split(r"[><=!;\[]", dep)[0].strip()
        if any(dep_name.startswith(p) for p in INTERNAL_PREFIXES):
            continue
        external.append(dep)
    return external


def get_entry_points(data: dict) -> dict[str, str]:
    """Extract command entry points from pyproject.toml."""
    entry_points = {}
    eps = data.get("project", {}).get("entry-points", {})

    # Check deepctl.commands
    for cmd_name, target in eps.get("deepctl.commands", {}).items():
        entry_points[cmd_name] = target

    # Check deepctl.subcommands.debug
    for cmd_name, target in eps.get("deepctl.subcommands.debug", {}).items():
        entry_points[cmd_name] = target

    # Check deepctl.plugins
    for cmd_name, target in eps.get("deepctl.plugins", {}).items():
        entry_points[cmd_name] = target

    return entry_points


def render_install_section() -> list[str]:
    """Render the common installation section for built-in packages."""
    return [
        "## Installation",
        "",
        "This package is included with deepctl and does not need to be "
        "installed separately.",
        "",
        "### Install deepctl",
        "",
        "```bash",
        "# Install with pip",
        "pip install deepctl",
        "",
        "# Or install with uv",
        "uv tool install deepctl",
        "",
        "# Or install with pipx",
        "pipx install deepctl",
        "",
        "# Or run without installing",
        "uvx deepctl --help",
        "pipx run deepctl --help",
        "```",
    ]


def render_extra_content(package_dir: Path) -> list[str]:
    """Load README.extra.md if it exists for package-specific content."""
    extra_path = package_dir / "README.extra.md"
    if extra_path.exists():
        return ["", extra_path.read_text().rstrip()]
    return []


def render_command_readme(
    name: str,
    description: str,
    entry_points: dict,
    external_deps: list,
    package_dir: Path,
) -> str:
    """Render README for a command package (deepctl-cmd-*)."""
    lines = [
        f"# {name}",
        "",
        "> Part of [deepctl](https://github.com/deepgram/cli)"
        " — Official Deepgram CLI",
        "",
        description,
        "",
    ]
    lines.extend(render_install_section())

    lines.extend(
        [
            "",
            "## Commands",
            "",
            "| Command | Entry Point |",
            "|---------|-------------|",
        ]
    )
    for cmd, target in sorted(entry_points.items()):
        lines.append(f"| `deepctl {cmd}` | `{target}` |")

    lines.extend(render_extra_content(package_dir))

    lines.extend(["", "## Dependencies", ""])
    if external_deps:
        for dep in external_deps:
            lines.append(f"- `{dep}`")
    else:
        lines.append("No external dependencies.")

    lines.extend(
        ["", "## License", "", "MIT — see [LICENSE](../../LICENSE)", ""]
    )
    return "\n".join(lines)


def render_debug_subcommand_readme(
    name: str,
    description: str,
    entry_points: dict,
    external_deps: list,
    package_dir: Path,
) -> str:
    """Render README for a debug subcommand package."""
    lines = [
        f"# {name}",
        "",
        "> Part of [deepctl](https://github.com/deepgram/cli)"
        " — Official Deepgram CLI",
        "",
        description,
        "",
        "This is a subcommand of `deepctl debug`.",
        "",
    ]
    lines.extend(render_install_section())

    lines.extend(
        [
            "",
            "## Commands",
            "",
            "| Command | Entry Point |",
            "|---------|-------------|",
        ]
    )
    for cmd, target in sorted(entry_points.items()):
        lines.append(f"| `deepctl debug {cmd}` | `{target}` |")

    lines.extend(render_extra_content(package_dir))

    lines.extend(["", "## Dependencies", ""])
    if external_deps:
        for dep in external_deps:
            lines.append(f"- `{dep}`")
    else:
        lines.append("No external dependencies.")

    lines.extend(
        ["", "## License", "", "MIT — see [LICENSE](../../LICENSE)", ""]
    )
    return "\n".join(lines)


def render_core_readme(
    name: str,
    description: str,
    external_deps: list,
    package_dir: Path,
) -> str:
    """Render README for a core/utility package."""
    lines = [
        f"# {name}",
        "",
        "> Part of [deepctl](https://github.com/deepgram/cli)"
        " — Official Deepgram CLI",
        "",
        description,
        "",
        "This package provides internal APIs for deepctl and its command"
        " packages. It is not intended for direct use.",
        "",
    ]
    lines.extend(render_install_section())

    lines.extend(render_extra_content(package_dir))

    lines.extend(["", "## Dependencies", ""])
    if external_deps:
        for dep in external_deps:
            lines.append(f"- `{dep}`")
    else:
        lines.append("No external dependencies.")

    lines.extend(
        ["", "## License", "", "MIT — see [LICENSE](../../LICENSE)", ""]
    )
    return "\n".join(lines)


def generate_readme(package_dir: Path) -> str:
    """Generate README content for a single package."""
    data = load_pyproject(package_dir)
    project = data["project"]
    name = project["name"]
    description = project.get("description", name)
    deps = project.get("dependencies", [])
    external_deps = get_external_deps(deps)
    entry_points = get_entry_points(data)
    kind = classify_package(name, data)

    if kind == "debug-subcommand":
        return render_debug_subcommand_readme(
            name, description, entry_points, external_deps, package_dir
        )
    elif kind == "command":
        return render_command_readme(
            name, description, entry_points, external_deps, package_dir
        )
    else:
        return render_core_readme(
            name, description, external_deps, package_dir
        )


def get_package_dirs(single: str | None = None) -> list[Path]:
    """Return list of package directories to process."""
    if single:
        pkg_dir = PACKAGES_DIR / single
        if not pkg_dir.exists():
            print(f"Package not found: {pkg_dir}")
            sys.exit(1)
        return [pkg_dir]

    dirs = sorted(
        d
        for d in PACKAGES_DIR.iterdir()
        if d.is_dir() and (d / "pyproject.toml").exists()
    )
    return [d for d in dirs if d.name not in SKIP_PACKAGES]


# ── Root README section generators ──────────────────────────────


def get_all_packages() -> list[dict]:
    """Load metadata for all packages in the workspace."""
    packages = []
    for pkg_dir in sorted(PACKAGES_DIR.iterdir()):
        if not pkg_dir.is_dir() or not (pkg_dir / "pyproject.toml").exists():
            continue
        data = load_pyproject(pkg_dir)
        project = data["project"]
        eps = project.get("entry-points", {})
        packages.append(
            {
                "dir_name": pkg_dir.name,
                "name": project["name"],
                "description": project.get(
                    "description", project["name"]
                ),
                "commands": eps.get("deepctl.commands", {}),
                "debug_subcommands": eps.get(
                    "deepctl.subcommands.debug", {}
                ),
            }
        )
    return packages


def generate_commands_section(packages: list[dict]) -> str:
    """Generate the commands markdown table."""
    rows = []
    for pkg in packages:
        if pkg["dir_name"] in SKIP_PACKAGES:
            continue
        for cmd_name in pkg["commands"]:
            rows.append((f"`deepctl {cmd_name}`", pkg["description"]))
        for cmd_name in pkg["debug_subcommands"]:
            rows.append(
                (f"`deepctl debug {cmd_name}`", pkg["description"])
            )
    rows.sort(key=lambda r: r[0])
    lines = [
        "| Command | Description |",
        "|---------|-------------|",
    ]
    for cmd, desc in rows:
        lines.append(f"| {cmd} | {desc} |")
    return "\n".join(lines)


def generate_packages_section(packages: list[dict]) -> str:
    """Generate the packages markdown table."""
    lines = [
        "| Package | Description |",
        "|---------|-------------|",
    ]
    for pkg in sorted(packages, key=lambda p: p["name"]):
        lines.append(
            f"| [`{pkg['name']}`](packages/{pkg['dir_name']})"
            f" | {pkg['description']} |"
        )
    return "\n".join(lines)


def generate_architecture_section(packages: list[dict]) -> str:
    """Generate the ASCII architecture tree."""
    comment_col = 38

    def tree_line(prefix: str, name: str, desc: str) -> str:
        line = f"{prefix}{name}"
        padding = max(1, comment_col - len(line))
        return f"{line}{' ' * padding}# {desc}"

    lines = ["```", "cli/"]
    lines.append(
        tree_line("├── ", "src/deepctl/", "Main CLI entry point")
    )
    lines.append("├── packages/")

    sorted_pkgs = sorted(packages, key=lambda p: p["dir_name"])
    for i, pkg in enumerate(sorted_pkgs):
        is_last = i == len(sorted_pkgs) - 1
        connector = "└── " if is_last else "├── "
        lines.append(
            tree_line(
                f"│   {connector}",
                pkg["dir_name"] + "/",
                pkg["description"],
            )
        )

    lines.append(
        tree_line("├── ", "tests/", "Integration tests")
    )
    lines.append(
        tree_line("└── ", "Makefile", "Development tasks")
    )
    lines.append("```")
    return "\n".join(lines)


def replace_section(
    content: str, section: str, replacement: str
) -> str:
    """Replace content between BEGIN:section and END:section markers."""
    pattern = re.compile(
        rf"(<!-- BEGIN:{re.escape(section)} -->\n)"
        rf".*?"
        rf"(<!-- END:{re.escape(section)} -->)",
        re.DOTALL,
    )

    def repl(m: re.Match) -> str:
        return m.group(1) + replacement + "\n" + m.group(2)

    return pattern.sub(repl, content)


def update_root_readme(
    *, dry_run: bool = False, check: bool = False
) -> bool:
    """Update auto-generated sections in the root README.md.

    Returns True if the file was (or needs to be) changed.
    """
    readme_path = ROOT / "README.md"
    content = readme_path.read_text()

    packages = get_all_packages()

    new_content = content
    new_content = replace_section(
        new_content, "commands", generate_commands_section(packages)
    )
    new_content = replace_section(
        new_content, "packages", generate_packages_section(packages)
    )
    new_content = replace_section(
        new_content,
        "architecture",
        generate_architecture_section(packages),
    )

    if content == new_content:
        if dry_run:
            print("  README.md — up to date")
        return False

    if check:
        print("  README.md — out of date")
        return True

    if dry_run:
        print("  README.md — would update")
        return True

    readme_path.write_text(new_content)
    print("  README.md — updated")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Generate README files from pyproject.toml metadata"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any README is out of date",
    )
    parser.add_argument(
        "--package",
        type=str,
        default=None,
        help="Generate README for a single package (e.g. deepctl-core)",
    )
    args = parser.parse_args()

    package_dirs = get_package_dirs(args.package)
    stale = []

    for pkg_dir in package_dirs:
        readme_path = pkg_dir / "README.md"
        new_content = generate_readme(pkg_dir)

        existing = readme_path.read_text() if readme_path.exists() else ""

        if existing == new_content:
            if args.dry_run:
                print(f"  {pkg_dir.name}/README.md — up to date")
            continue

        stale.append(pkg_dir.name)

        if args.check:
            print(f"  {pkg_dir.name}/README.md — out of date")
            continue

        if args.dry_run:
            print(f"  {pkg_dir.name}/README.md — would update")
            print("---")
            print(new_content)
            print("---")
            continue

        readme_path.write_text(new_content)
        print(f"  {pkg_dir.name}/README.md — updated")

    # Update root README sections (skip when targeting a single package)
    if not args.package:
        root_changed = update_root_readme(
            dry_run=args.dry_run, check=args.check
        )
        if root_changed:
            stale.append("README.md")

    if args.check and stale:
        print(
            f"\n{len(stale)} README(s) out of date. "
            "Run: python scripts/generate_readmes.py"
        )
        sys.exit(1)

    if not args.check and not args.dry_run:
        total = len(package_dirs) + (0 if args.package else 1)
        updated = len(stale)
        print(
            f"\nDone: {updated} updated, "
            f"{total - updated} already current"
        )


if __name__ == "__main__":
    main()
