# PR: Add OpenCode MCP Integration Support

## Summary

This PR adds native support for integrating Not-Happy-Jan with the OpenCode CLI agent through MCP (Model Context Protocol).

## Changes

### 1. Documentation (`docs/integration.md`)

Added comprehensive OpenCode integration section including:
- Configuration instructions for `~/.config/opencode/opencode.json`
- Installation command: `nhj install-opencode`
- Usage examples with the `nhj_vibe` MCP tool
- Requirements and verification steps

### 2. CLI Enhancement (`src/nhj/cli.py`)

Added new command `nhj install-opencode`:
- Generates OpenCode MCP configuration
- Updates existing `opencode.json` without overwriting other MCP servers
- Provides clear next-step instructions

### 3. Resource Management (`src/nhj/resources.py`)

Added new functions:
- `opencode_config_path()`: Returns OpenCode config file path
- `opencode_mcp_config_template()`: Generates MCP configuration JSON

### 4. Issue Template (`.github/ISSUE_TEMPLATE/opencode-integration.md`)

Created feature request issue template for tracking OpenCode integration work.

## Testing

```bash
# Verify CLI command loads
.venv/bin/python -c "from nhj.cli import install_opencode; print('OK')"

# Test MCP server starts
nhj serve-mcp &

# Install OpenCode config
nhj install-opencode

# Verify configuration was added
cat ~/.config/opencode/opencode.json | jq '.mcp."not-happy-jan"'
```

## Breaking Changes

None. This is a pure addition with no breaking changes to existing functionality.

## Backward Compatibility

All existing Claude Code, Cursor, Aider, and other integrations continue to work unchanged.

## Related Issues

- Issue: [Feature] Add OpenCode MCP Integration Support

## Notes

OpenCode uses MCP for tool integration but does not have a skill system like Claude Code. The integration relies solely on the MCP server configuration.