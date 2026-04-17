# mcp-controller
Experimental implementation of intelligent MCP controller.

## Documentation

* **[Architecture & Concepts](docs/arch_and_concepts.md)** — Overall design plan, exposure patterns (Resources, Tools, Prompts), multi-controller data model, and mock mode overview.
* **[Contributing Guide](docs/contributing.md)** — Step-by-step conventions, project layout, and checklists for adding a new Network Function (NF) controller.
* **[MCP Inspector Setup](docs/mcp-server-inspector-setup.md)** — A practical guide for installing and using the MCP Inspector to test the MCP server locally.
* **[Kubernetes Implementation Notes](docs/k8s_notes.md)** — Details on CRD code generation, Kubernetes client structure, and related dependencies.
* **[Troubleshooting Inotify Limits](docs/inotify-limits.md)** — Quick fixes for the "Too many open files" error often encountered during development.
* **[Change Log](docs/change_log.md)** — Chronological record of new features, bug fixes, and architectural updates.

## Running with Docker Compose

To build and start the MCP controller using Docker Compose:

```bash
# Build the image and start the container in the background
docker compose up -d --build --force-recreate

# View the logs
docker compose logs -f
```

## Running in Mock Mode

The server supports a record/replay mock system, allowing you to run the MCP server and exercise all tools and resources without a live Kubernetes, Prometheus, or Loki backend.

### Replay Mode (Offline)

Run the server using pre-recorded responses (no backend connections are made):

```bash
./uv-run.sh --mock
# Or via env var: MCP_MOCK=true python -m mcp_controller
```

To run replay mode via Docker Compose, uncomment `MCP_MOCK: "true"` under `environment:` in [docker-compose.yml](docker-compose.yml) and keep the read-only volume mount active:

```yaml
environment:
  MCP_MOCK: "true"
  MCP_MOCK_DATA_DIR: "/app/tests/mocks/data"
volumes:
  - "./tests/mocks/data:/app/tests/mocks/data:ro" # For mock data playback
```

Then start the container:

```bash
docker compose up -d --build --force-recreate
```

### Record Mode

Run against live backends and save the arguments and results to `tests/mocks/data/` for future offline use:

```bash
./uv-run.sh --mock-data-record --config lab.yaml
# Or via env var: MCP_MOCK_DATA_RECORD=true python -m mcp_controller --config lab.yaml
```

To run record mode via Docker Compose, uncomment `MCP_MOCK_DATA_RECORD: "true"` under `environment:` in [docker-compose.yml](docker-compose.yml) and swap the volume mount from `:ro` to `:rw` so recorded fixtures can be written back to the host:

```yaml
environment:
  MCP_MOCK_DATA_RECORD: "true"
  MCP_MOCK_DATA_DIR: "/app/tests/mocks/data"
volumes:
  - "./tests/mocks/data:/app/tests/mocks/data:rw" # For mock data recording
```
