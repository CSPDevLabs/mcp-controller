# MCP Inspector Setup Guide

A practical guide to setting up and using the MCP Inspector for testing your MCP server.

## Prerequisites

### Node.js (v18+)

The MCP Inspector is a Node.js tool and requires **Node.js 18 or later**.
Older versions (like v16) will fail with cryptic errors because they lack
built-in Fetch API globals (`Request`, `Response`) that the Inspector depends on.

#### Installing nvm (Node Version Manager)

**nvm** lets you install and switch between multiple Node.js versions without
root access. Everything lives under `~/.nvm/`.

```bash
# Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash

# Reload your shell (or restart the terminal)
source ~/.zshrc   # for zsh
# source ~/.bashrc  # for bash
```

#### Installing Node.js via nvm

```bash
# Install Node.js 24.14.1
nvm install 24.14.1

# Verify
node --version
# v20.x.x
```

Useful nvm commands:

| Command                     | Description                        |
| --------------------------- | ---------------------------------- |
| `nvm install <version>`     | Install a specific Node.js version |
| `nvm use <version>`         | Switch to an installed version     |
| `nvm ls`                    | List installed versions            |
| `nvm alias default <ver>`   | Set the default version            |

### Python MCP Server

Make sure your MCP server is installed and runnable:

```bash
pip install -e ".[dev]"
```

## Running the MCP Inspector

### Step 1 — Start your MCP server

```bash
./uv-run.sh --port 8001
```

By default this binds to `0.0.0.0:8001` with `streamable-http` transport.
The server exposes its MCP endpoint at `/mcp`.

### Step 2 — Launch the Inspector

Always use `@latest` to avoid stale cached versions:

```bash
npx --yes @modelcontextprotocol/inspector@latest
```

The Inspector UI will open in your browser (typically at `http://localhost:6274`).

### Step 3 — Connect to your server

In the Inspector UI:

1. Set **Transport Type** to **Streamable HTTP**
2. Set **URL** to `http://localhost:8001/mcp`
3. Click **Connect**

You should see the server info and can now browse resources, call tools,
and test prompts interactively.

## Transport Types

MCP supports three transport types. Which one you use affects how you connect
the Inspector.

| Transport          | When to use                          | Inspector URL            |
| ------------------ | ------------------------------------ | ------------------------ |
| `stdio`            | CLI tools, local integrations        | N/A (use command mode)   |
| `sse`              | Older HTTP servers, broad compat     | `http://host:port/sse`   |
| `streamable-http`  | Modern HTTP servers (recommended)    | `http://host:port/mcp`   |

The transport is set in `__main__.py`:

```python
server.run(transport="streamable-http")
```

## Troubleshooting

### "Class extends value undefined is not a constructor or null"

**Cause:** Node.js version is too old (< 18).

**Fix:**
```bash
nvm install 20 && nvm use 20
```

### "Not Acceptable: Client must accept text/event-stream"

**Cause:** The Inspector version is outdated and doesn't send the correct
`Accept` header for `streamable-http` transport. The Streamable HTTP protocol
requires `Accept: application/json, text/event-stream` (both types).

**Fix:**
```bash
# Clear the npx cache and fetch the latest version
npx --yes @modelcontextprotocol/inspector@latest
```

### "Error Connecting to MCP Inspector Proxy"

Check the terminal where you launched the Inspector for the actual error.
Common causes:

- Server not running or wrong port
- Transport type mismatch in the Inspector UI
- Wrong URL path (`/mcp` for streamable-http, `/sse` for SSE)

### Port already in use

If your server fails to start with `[Errno 98] address already in use`:

```bash
# Find what's using the port
ss -tlnp | grep 8000

# Kill it or pick a different port
python -m mcp_controller --port 8001
```

## Quick Verification with curl

You can test your server without the Inspector using curl:

```bash
curl -X POST \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-03-26",
      "capabilities": {},
      "clientInfo": {"name": "curl-test", "version": "0.1"}
    }
  }' \
  http://localhost:8000/mcp
```

A successful response returns `200 OK` with the server capabilities as an
SSE event stream.
