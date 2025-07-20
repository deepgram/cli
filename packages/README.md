# Workspace Packages

This directory contains additional packages that are part of the Deepgram CLI workspace.

## Structure

Each package should follow this structure:

```
packages/
└── package-name/
    ├── pyproject.toml      # Package configuration
    ├── README.md           # Package documentation
    └── src/
        └── package_module/ # Python package source
            └── __init__.py
```

## Creating a New Package

1. Create a new directory under `packages/`
2. Add a `pyproject.toml` with the package configuration
3. Create the source structure under `src/`
4. The package will automatically be included in the workspace

## Example Package Types

- **Plugins**: Extensions for the CLI (e.g., `deepgram-plugin-audio`)
- **SDK Extras**: Additional utilities for the Deepgram SDK
- **Shared Libraries**: Common code used across multiple packages
- **Tools**: Development or auxiliary tools

## Package Naming Convention

- Use lowercase with hyphens for directory names
- Use underscores for Python module names
- Prefix with `deepgram-` for clarity
