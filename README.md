## Setup 

Create a virtual environment and download all dependencies:

```bash
uv venv
source .venv/bin/activate
uv sync
```

To run the production server:

```bash
fastapi run main.py --host 0.0.0.0 --port 8000
```

## MCP (Remote Tools)

This server exposes a Model Context Protocol (MCP) endpoint for all API routes.
Once running, the MCP streamable HTTP endpoint is available at:

```text
http://<host>:<port>/mcp
```
