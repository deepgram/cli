#!/usr/bin/env python3
"""Regenerate the Homebrew formula for deepctl from a template + PyPI.

Reads scripts/templates/deepgram.rb.template, substitutes the new tarball URL,
sha256, and freshly-generated transitive resource blocks, and writes the result
to the target formula path. Used both by the release-automation workflow and
manually for emergency bumps.

Usage:
    python scripts/bump_brew_formula.py --version 0.2.18 \\
        --formula path/to/homebrew-tap/Formula/deepgram.rb

    python scripts/bump_brew_formula.py --version 0.2.18 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = ROOT / "scripts" / "templates" / "deepgram.rb.template"
PACKAGE = "deepctl"
RESOURCE_INDENT = "  "


class BumpError(RuntimeError):
    pass


def fetch_pypi_sdist(package: str, version: str) -> tuple[str, str]:
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        raise BumpError(f"Failed to fetch PyPI metadata for {package}=={version}: {e}")

    for asset in data.get("urls", []):
        if asset.get("packagetype") == "sdist":
            digest = asset.get("digests", {}).get("sha256")
            href = asset.get("url")
            if digest and href:
                return href, digest
    raise BumpError(
        f"No sdist found for {package}=={version} on PyPI. "
        "Verify the release was published before bumping the formula."
    )


def run_poet(version: str, python_exe: str) -> str:
    with tempfile.TemporaryDirectory(prefix="deepctl-poet-") as tmp:
        venv = Path(tmp) / "venv"
        try:
            subprocess.run(
                [python_exe, "-m", "venv", str(venv)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise BumpError(
                f"Failed to create venv with {python_exe}: {e.stderr or e.stdout}"
            )

        pip = venv / "bin" / "pip"
        poet = venv / "bin" / "poet"

        # homebrew-pypi-poet uses pkg_resources (removed from setuptools 80+),
        # so pin setuptools below that. This pin is solely for the bump tool's
        # private venv; it has no effect on the formula or its dependencies.
        deps = [
            "--upgrade",
            "pip",
            "setuptools<80",
            f"{PACKAGE}=={version}",
            "homebrew-pypi-poet",
        ]
        try:
            subprocess.run(
                [str(pip), "install", "--quiet", *deps],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise BumpError(
                f"Failed to install bump-tool dependencies "
                f"(deepctl=={version}, homebrew-pypi-poet, setuptools<80): "
                f"{e.stderr or e.stdout}"
            )

        try:
            result = subprocess.run(
                [str(poet), "-f", PACKAGE],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise BumpError(f"homebrew-pypi-poet failed: {e.stderr or e.stdout}")

        return result.stdout


def extract_resource_blocks(poet_output: str) -> str:
    """Extract just the `resource "..." do ... end` blocks from poet's output.

    Poet emits a full skeleton formula. We only want the resource blocks; the
    surrounding header/install/test lines come from our template instead.
    """
    pattern = re.compile(
        r'^(  resource "[^"]+" do\n(?:    .+\n)+  end)\n?',
        re.MULTILINE,
    )
    blocks = pattern.findall(poet_output)
    if not blocks:
        raise BumpError(
            "homebrew-pypi-poet produced no resource blocks. "
            "Output may have changed format; inspect manually."
        )
    return "\n\n".join(blocks)


def render_formula(
    template: str,
    *,
    tarball_url: str,
    tarball_sha256: str,
    resources: str,
) -> str:
    substitutions = {
        "{{TARBALL_URL}}": tarball_url,
        "{{TARBALL_SHA256}}": tarball_sha256,
        "{{RESOURCES}}": resources,
    }
    rendered = template
    for placeholder, value in substitutions.items():
        if placeholder not in rendered:
            raise BumpError(
                f"Template is missing placeholder {placeholder}. "
                "Check scripts/templates/deepgram.rb.template."
            )
        rendered = rendered.replace(placeholder, value)
    return rendered


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        required=True,
        help=f"{PACKAGE} version to bump to (e.g. 0.2.18). Must already be on PyPI.",
    )
    parser.add_argument(
        "--formula",
        type=Path,
        help="Path to the target formula file (e.g. homebrew-tap/Formula/deepgram.rb). "
        "Required unless --dry-run is set.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help=f"Path to the formula template (default: {DEFAULT_TEMPLATE}).",
    )
    parser.add_argument(
        "--python",
        default="python3.13",
        help="Python interpreter used to create the bump tool's private venv "
        "(default: python3.13). Must match the depends_on in the template.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the rendered formula to stdout instead of writing it.",
    )
    args = parser.parse_args(argv)
    if not args.dry_run and args.formula is None:
        parser.error("--formula is required unless --dry-run is set")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        template = args.template.read_text()
    except FileNotFoundError:
        print(f"error: template not found at {args.template}", file=sys.stderr)
        return 2

    try:
        url, sha256 = fetch_pypi_sdist(PACKAGE, args.version)
        print(f"PyPI sdist: {url}", file=sys.stderr)
        print(f"sha256:     {sha256}", file=sys.stderr)

        print(
            f"Generating resources via homebrew-pypi-poet ({args.python})...",
            file=sys.stderr,
        )
        poet_output = run_poet(args.version, args.python)
        resources = extract_resource_blocks(poet_output)
        n_resources = resources.count("resource ")
        print(f"Extracted {n_resources} resource blocks", file=sys.stderr)

        rendered = render_formula(
            template,
            tarball_url=url,
            tarball_sha256=sha256,
            resources=resources,
        )
    except BumpError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        sys.stdout.write(rendered)
        return 0

    args.formula.parent.mkdir(parents=True, exist_ok=True)
    args.formula.write_text(rendered)
    print(f"Wrote {args.formula} ({rendered.count(chr(10))} lines)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
