# Plugin Registry Future Plans

This document outlines the future plans for evolving the hardcoded plugin registry into a dynamic, URL-based system.

## Current Implementation

The plugin search functionality currently uses a hardcoded list of plugins in the `_get_plugin_registry()` method. This was implemented as a first step to provide immediate value while laying the groundwork for a more sophisticated system.

## Future Implementation

### 1. .well-known URL

The plugin registry will be hosted at a well-known URL, following industry standards:

```
https://deepgram.com/.well-known/deepctl-plugins.json
```

### 2. Registry Format

The registry will use a JSON format similar to:

```json
{
  "version": "1.0",
  "updated": "2024-01-15T00:00:00Z",
  "plugins": [
    {
      "name": "deepctl-plugin-example",
      "description": "Example plugin demonstrating the plugin system",
      "version": "0.1.8",
      "author": "Deepgram DevRel",
      "url": "https://github.com/deepgram/deepctl",
      "keywords": ["example", "demo", "plugin"],
      "install_name": "deepctl-plugin-example",
      "requirements": {
        "deepctl": ">=0.1.0",
        "python": ">=3.10"
      }
    }
  ]
}
```

### 3. Implementation Changes

Update the `_get_plugin_registry()` method to:

```python
def _get_plugin_registry(self) -> list[PluginRegistryEntry]:
    """Get the plugin registry from the well-known URL."""
    try:
        # Try to fetch from the registry URL
        import requests
        response = requests.get(
            "https://deepgram.com/.well-known/deepctl-plugins.json",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            return [
                PluginRegistryEntry(**plugin)
                for plugin in data.get("plugins", [])
            ]
    except Exception as e:
        # Fall back to cached registry or hardcoded list
        print_info(f"Could not fetch plugin registry: {e}")

    # Return hardcoded list as fallback
    return self._get_fallback_registry()
```

### 4. Caching

Implement local caching to improve performance and provide offline functionality:

- Cache location: `~/.deepctl/cache/plugin-registry.json`
- Cache duration: 24 hours
- Update cache on successful fetch
- Use cache when network is unavailable

### 5. Plugin Submission Process

Create a process for plugin developers to submit their plugins:

1. **GitHub Repository**: Plugin must be in a public GitHub repository
2. **Quality Standards**: Must meet code quality and documentation standards
3. **Security Review**: Basic security review for malicious code
4. **Submission PR**: Submit via PR to the registry repository
5. **Automated Testing**: CI/CD validates the plugin works with current deepctl

### 6. Registry Features

Future enhancements to the registry:

- **Categories**: Group plugins by functionality (audio, video, analysis, etc.)
- **Popularity**: Track installation counts and ratings
- **Dependencies**: Show plugin dependencies and compatibility
- **Changelogs**: Link to plugin changelogs
- **Screenshots**: Support for plugin UI screenshots (for plugins with UI)

### 7. Security Considerations

- **HTTPS Only**: Registry must be served over HTTPS
- **Signature Verification**: Optional GPG signature verification
- **Checksum Validation**: Verify plugin integrity after download
- **Vulnerability Scanning**: Regular security scans of registry plugins

### 8. Alternative Registries

Support for alternative/private registries:

```bash
# Use a custom registry
deepctl plugin search --registry https://mycompany.com/deepctl-plugins.json

# Configure default registry
deepctl config set plugin.registry https://mycompany.com/deepctl-plugins.json
```

### 9. Plugin Discovery Service

Long-term vision includes a full plugin discovery service with:

- Web interface for browsing plugins
- API for programmatic access
- User accounts for plugin developers
- Analytics for plugin authors
- Integration with Deepgram's developer portal

## Implementation Timeline

1. **Phase 1** (Current): Hardcoded registry with search functionality
2. **Phase 2** (Q2 2024): Basic .well-known URL with static JSON
3. **Phase 3** (Q3 2024): Caching and fallback mechanisms
4. **Phase 4** (Q4 2024): Plugin submission process
5. **Phase 5** (2025): Full plugin discovery service

## Benefits

- **Discoverability**: Users can easily find plugins
- **Trust**: Curated registry ensures quality
- **Updates**: Users notified of plugin updates
- **Community**: Encourages plugin development
- **Analytics**: Understand plugin usage patterns
