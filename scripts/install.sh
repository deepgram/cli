#!/bin/sh
# Deepgram CLI installer
# Usage: curl -fsSL https://deepgram.com/install.sh | sh
set -e

PACKAGE="deepctl"

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

# --- Main ---

say ""
say "  Deepgram CLI Installer"
say "  ======================"
say ""

# Detect OS
OS="$(uname -s)"
case "$OS" in
    Linux|Darwin) ;;
    *) err "Unsupported operating system: $OS" ;;
esac

# Try install methods in order of preference
if has uv; then
    say "Found uv, installing ${PACKAGE}..."
    uv tool install "$PACKAGE"
elif has pipx; then
    say "Found pipx, installing ${PACKAGE}..."
    pipx install "$PACKAGE"
elif has pip3; then
    say "Found pip3, installing ${PACKAGE}..."
    pip3 install --user "$PACKAGE"
elif has pip; then
    say "Found pip, installing ${PACKAGE}..."
    pip install --user "$PACKAGE"
else
    say "No Python package manager found. Installing uv first..."
    say ""
    install_uv
    say ""
    say "Installing ${PACKAGE}..."
    uv tool install "$PACKAGE"
fi

say ""

# Verify installation
if has deepctl; then
    say "Deepgram CLI installed successfully!"
    say ""
    deepctl --version
    say ""
    say "Get started:"
    say "  deepctl login"
    say "  deepctl --help"
else
    say "Installation complete, but 'deepctl' is not in your PATH."
    say ""
    say "You may need to restart your shell or add one of these to your PATH:"
    say "  ~/.local/bin"
    say "  ~/.local/share/uv/tools/${PACKAGE}/bin"
    say ""
    say "Then run: deepctl --help"
fi
