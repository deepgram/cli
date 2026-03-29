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

# --- Install uv if no Python tooling found ---

install_uv() {
    say "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
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
    PIP_FLAGS="--force-reinstall"
fi

# Try install methods in order of preference
if has uv; then
    say "Found uv, installing ${SPEC}..."
    uv tool install $UV_FLAGS "$SPEC"
elif has pipx; then
    say "Found pipx, installing ${SPEC}..."
    pipx install $PIPX_FLAGS "$SPEC"
elif has pip3; then
    say "Found pip3, installing ${SPEC}..."
    pip3 install --user $PIP_FLAGS "$SPEC"
elif has pip; then
    say "Found pip, installing ${SPEC}..."
    pip install --user $PIP_FLAGS "$SPEC"
else
    say "No Python package manager found. Installing uv first..."
    say ""
    install_uv
    say ""
    say "Installing ${SPEC}..."
    uv tool install $UV_FLAGS "$SPEC"
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
