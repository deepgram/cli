#!/bin/sh
# Deepgram CLI installer
# Usage:
#   curl -fsSL https://deepgram.com/install.sh | sh
#   curl -fsSL https://deepgram.com/install.sh | sh -s -- --force
#   curl -fsSL https://deepgram.com/install.sh | sh -s -- v0.2.1
#   curl -fsSL https://deepgram.com/install.sh | sh -s -- --force v0.2.1
#   curl -fsSL https://deepgram.com/install.sh | DEEPCTL_VERSION=0.2.1 sh
#   curl -fsSL https://deepgram.com/install.sh | DEEPCTL_FORCE=1 sh
set -e

PACKAGE="deepctl"
FORCE="${DEEPCTL_FORCE:-0}"
VERSION="${DEEPCTL_VERSION:-}"
METHOD=""

# --- Parse arguments ---

for arg in "$@"; do
    case "$arg" in
        --force|-f)
            FORCE=1
            ;;
        v*)
            VERSION="${arg#v}"
            ;;
        *)
            VERSION="$arg"
            ;;
    esac
done

# --- Helpers ---

say() {
    printf '%s\n' "$*"
}

err() {
    say "Error: $*" >&2
    exit 1
}

has() {
    command -v "$1" >/dev/null 2>&1
}

sha256_of() {
    if has sha256sum; then
        sha256sum "$1" | cut -d ' ' -f 1
    elif has shasum; then
        shasum -a 256 "$1" | cut -d ' ' -f 1
    elif has openssl; then
        openssl dgst -sha256 "$1" | sed 's/^.*= *//'
    else
        return 1
    fi
}

# --- Install uv if no Python tooling found ---

# The uv installer is pinned to one release and its sha256: it is downloaded,
# verified, and only then run, so a changed installer never runs. When
# sha256sum is available, that installer also checks the sha256 of the uv
# archive it downloads, which pins uv itself too.
# Bump all three together (the sha256 is the release's uv-installer.sh asset).
UV_VERSION="0.12.24"
UV_INSTALLER_SHA256="e8c19f8dbfaafcbeddc8d17bf9227505d918220eba2aeb543d6922bdc5bb9625"
# A mirror may serve the same file; it must still match UV_INSTALLER_SHA256.
UV_INSTALLER_URL="${DEEPCTL_UV_INSTALLER_URL:-https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-installer.sh}"
UV_MANUAL="install uv yourself (https://docs.astral.sh/uv/), then run this script again."

install_uv() {
    say "Installing uv ${UV_VERSION}..."
    UV_INSTALLER="$(mktemp "${TMPDIR:-/tmp}/uv-installer.XXXXXX")" \
        || err "Could not create a temporary file for the uv installer. Check that ${TMPDIR:-/tmp} is writable, or ${UV_MANUAL}"
    trap 'rm -f "$UV_INSTALLER"' EXIT
    trap 'rm -f "$UV_INSTALLER"; exit 130' INT TERM
    if ! curl -fsSL -o "$UV_INSTALLER" "$UV_INSTALLER_URL"; then
        rm -f "$UV_INSTALLER"
        err "Could not download the uv installer from ${UV_INSTALLER_URL}. Check your connection, or ${UV_MANUAL}"
    fi
    if ! UV_ACTUAL_SHA256="$(sha256_of "$UV_INSTALLER")"; then
        rm -f "$UV_INSTALLER"
        err "Found no sha256 tool (sha256sum, shasum or openssl) to check the uv installer, so it was not run. Install one, or ${UV_MANUAL}"
    fi
    if [ "$UV_ACTUAL_SHA256" != "$UV_INSTALLER_SHA256" ]; then
        rm -f "$UV_INSTALLER"
        err "The uv installer from ${UV_INSTALLER_URL} does not match the sha256 pinned for uv ${UV_VERSION}, so it was not run. If you set DEEPCTL_UV_INSTALLER_URL, check that it serves uv ${UV_VERSION}'s uv-installer.sh; otherwise ${UV_MANUAL}"
    fi
    UV_INSTALLER_RC=0
    sh "$UV_INSTALLER" || UV_INSTALLER_RC=$?
    rm -f "$UV_INSTALLER"
    trap - EXIT INT TERM
    [ "$UV_INSTALLER_RC" -eq 0 ] || err "The uv installer failed (exit ${UV_INSTALLER_RC}). See its output above, or ${UV_MANUAL}"
    # Source the env so uv is available in this session
    if [ -f "$HOME/.local/bin/env" ]; then
        . "$HOME/.local/bin/env"
    elif [ -f "$HOME/.cargo/env" ]; then
        . "$HOME/.cargo/env"
    fi
    # Verify
    if ! has uv; then
        export PATH="$HOME/.local/bin:$PATH"
    fi
    has uv || err "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
}

# --- Build install args ---

build_install_spec() {
    if [ -n "$VERSION" ]; then
        echo "${PACKAGE}==${VERSION}"
    else
        echo "${PACKAGE}"
    fi
}

# --- Main ---

say ""
say "  Deepgram CLI Installer"
say "  ======================"
say ""

SPEC="$(build_install_spec)"

if [ -n "$VERSION" ]; then
    say "Version: ${VERSION}"
fi
if [ "$FORCE" = "1" ]; then
    say "Force: reinstall"
fi
say ""

# Detect OS
OS="$(uname -s)"
case "$OS" in
    Linux|Darwin) ;;
    *) err "Unsupported operating system: $OS" ;;
esac

# Build flags
UV_FLAGS=""
PIPX_FLAGS=""
PIP_FLAGS=""
if [ "$FORCE" = "1" ]; then
    UV_FLAGS="--force"
    PIPX_FLAGS="--force"
    # -U makes the upgrade explicit (pip >= 20.3 already upgrades with --force-reinstall).
    PIP_FLAGS="-U --force-reinstall"
fi

# Try install methods in order of preference
if has uv; then
    say "Found uv, installing ${SPEC}..."
    uv tool install $UV_FLAGS "$SPEC"
    METHOD="uv_tool"
elif has pipx; then
    say "Found pipx, installing ${SPEC}..."
    pipx install $PIPX_FLAGS "$SPEC"
    METHOD="pipx"
elif has pip3; then
    say "Found pip3, installing ${SPEC}..."
    # shellcheck disable=SC2086 # flags are split on purpose
    pip3 install --user $PIP_FLAGS "$SPEC"
    METHOD="pip"
elif has pip; then
    say "Found pip, installing ${SPEC}..."
    # shellcheck disable=SC2086 # flags are split on purpose
    pip install --user $PIP_FLAGS "$SPEC"
    METHOD="pip"
else
    say "No Python package manager found. Installing uv first..."
    say ""
    install_uv
    say ""
    say "Installing ${SPEC}..."
    uv tool install $UV_FLAGS "$SPEC"
    METHOD="uv_tool"
fi

say ""

# Snapshot PATH before we modify it so we can advise the user later
ORIGINAL_PATH="$PATH"

# Ensure ~/.local/bin is on PATH for this session (covers pip --user, uv, pipx)
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac

# Verify installation
if has deepctl; then
    say "Deepgram CLI installed successfully!"
    say ""
    deepctl --record-install-method "$METHOD" >/dev/null 2>&1 || true
    deepctl --version
    say ""
    say "Available as:  dg  ·  deepctl  ·  deepgram"
    say ""
    say "Get started:"
    say "  dg login"
    say "  dg --help"
    say ""
    # Advise the user to persist PATH if ~/.local/bin was not already there
    case ":$ORIGINAL_PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *)
            say "Note: add the following to your shell profile to make these permanent:"
            say "  export PATH=\"\$HOME/.local/bin:\$PATH\""
            say ""
            ;;
    esac
else
    say "Installation complete, but the commands are not in your PATH."
    say ""
    say "Add the following to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    say "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    say ""
    say "Then open a new terminal and run: dg --help"
fi
