# Quick Start for Contributors

Welcome to deepctl development! This guide helps you get started quickly.

## 🚀 First Time Setup

```bash
# 1. Clone and enter the repository
git clone https://github.com/deepgram/cli.git
cd cli

# 2. Set up development environment (installs everything you need)
make dev-setup

# 3. Activate the virtual environment
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  # Windows
```

## 📋 Common Development Tasks

### **Daily Development Workflow**

```bash
make dev                    # Format code, fix lints, run tests (one command!)
```

### **Individual Tasks**

```bash
make test                   # Run tests
make format                 # Auto-format code
make lint                   # Check code quality
make build                  # Build packages
```

### **Before Committing**

```bash
make check                  # Quick quality check (format + lint, no tests)
# or
make dev                    # Full cycle (format + lint + test)
```

## 🍺 Homebrew Development

### **Build Homebrew Formulas**

```bash
make build-homebrew         # Build both wheel and binary formulas (recommended)

# Or individually:
make build-homebrew-wheels  # Wheel-based formula (uses PyPI)
make build-homebrew-binary  # Standalone binary formula
```

### **Test Locally**

```bash
make tap-install           # Install via local formula
make tap-uninstall         # Remove local installation
```

## 📦 Release Process (Maintainers Only)

```bash
make release               # Full automated release process
# Then: git push origin main --tags
```

## 🧪 Testing

```bash
make test                  # Quick tests (development)
make test-full             # All Python versions (3.10, 3.11, 3.12)
make ci                    # Full CI pipeline
```

## 🔧 Troubleshooting

### **Environment Issues**

```bash
make clean                 # Clean build artifacts
make clean-env             # Remove virtual environment
make dev-setup             # Recreate environment
```

### **See All Available Commands**

```bash
make help                  # Organized help (recommended)
make help-all              # All targets alphabetically
```

### **Project Information**

```bash
make info                  # Show Python/uv versions, paths, etc.
```

## 📚 Key Files to Know

- **`Makefile`** - All development commands
- **`pyproject.toml`** - Main project configuration
- **`packages/`** - Individual command packages
- **`src/deepctl/`** - Main CLI entry point
- **`docs/`** - Documentation (like this file!)

## 🎯 Quick Tips

1. **Always run `make dev` before committing** - it catches most issues
2. **Use `make help`** - shows the most common commands organized by category
3. **The project uses `uv`** - it's faster than pip and handles everything automatically
4. **All packages are versioned together** - one version bump updates everything
5. **Plugin system is modular** - each command is a separate package

## 🤝 Getting Help

- **Makefile commands**: `make help` or `make help-all`
- **Architecture**: See `docs/` directory for detailed documentation
- **Issues**: Check existing GitHub issues or create a new one

## 📖 Next Steps

Once you're comfortable with the basics:

- Read `docs/Development Guide With Uv.md` for detailed development info
- Check `docs/Architecture and Design.md` to understand the codebase structure
- Look at `docs/Testing and Test Strategy.md` for testing guidelines

Happy coding! 🎉
