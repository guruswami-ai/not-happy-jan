---
name: OpenCode Integration
about: Add support for OpenCode MCP integration
title: "[Feature] Add OpenCode MCP Integration Support"
labels: enhancement, integration
assignees: ""
---

## Summary

Add native OpenCode MCP server configuration support to enable not-happy-jan feedback system with OpenCode CLI agent.

## Motivation

OpenCode is a CLI-based AI coding agent that uses MCP (Model Context Protocol) for tool integration. Not-Happy-Jan already exposes an MCP server but lacks OpenCode-specific configuration documentation and setup.

## Current State

- ✅ MCP server exists (`src/nhj/mcp_server.py`)
- ✅ Works with Claude Code via hooks and MCP tool
- ✅ Can be used by any MCP-compatible agent
- ❌ No OpenCode-specific documentation
- ❌ No OpenCode installation/integration command

## Proposed Changes

### 1. Documentation Updates (`docs/integration.md`)

Add OpenCode-specific configuration section:

```json
// ~/.config/opencode/opencode.json
{
  "mcp": {
    "not-happy-jan": {
      "type": "local",
      "command": ["nhj", "serve-mcp"],
      "enabled": true
    }
  }
}
```

### 2. New CLI Command

Add `nhj install-opencode` command to:
- Generate OpenCode MCP configuration
- Copy skill file (if applicable)
- Verify connectivity

### 3. Example Usage

Document how to trigger feedback in OpenCode:
- Direct MCP tool calls: `nhj_vibe(intent="ok", message="Task completed")`
- Any MCP-compatible calling convention

## Implementation Plan

- [ ] Update `docs/integration.md` with OpenCode configuration
- [ ] Add `install-opencode` command to CLI (`src/nhj/cli.py`)
- [ ] Create OpenCode skill file if platform supports skills
- [ ] Test MCP server connectivity with OpenCode
- [ ] Update installation documentation

## Testing

```bash
# Start MCP server
nhj serve-mcp &

# Configure OpenCode to connect
# Test with nhj_vibe tool calls

# Verify feedback triggers:
nhj test ok
nhj test err -m "Build failed"
```

## Additional Context

OpenCode uses MCP for tool integration and stores configuration in `~/.config/opencode/opencode.json`. The MCP server should work with OpenCode's stdio transport mode.

See: [OpenCode Documentation](https://opencode.ai)