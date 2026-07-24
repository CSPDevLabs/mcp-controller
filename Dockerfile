# ---------- build stage ----------
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast, reproducible dependency resolution.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Copy dependency metadata
COPY pyproject.toml uv.lock ./

# Install production dependencies into a virtual environment.
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source.
COPY src/ src/

# Build a real wheel and install it into the venv so the package lands in
# site-packages — `uv sync --no-editable` skips the project when the lockfile
# pre-dates the [build-system] table and has no wheel entry for the project.
RUN uv build --wheel --out-dir /tmp/dist && \
    uv pip install --no-deps /tmp/dist/*.whl

# ---------- runtime stage ----------
FROM python:3.12-slim

WORKDIR /app

# Copy the virtual environment from the builder.
COPY --from=builder /app/.venv .venv

# Copy the entrypoint script.
COPY entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh

# # Copy application source so the entrypoint can locate it
# COPY src/ src/

# Ensure the venv Python is used by default.
ENV PATH="/app/.venv/bin:$PATH"
# ENV PYTHONPATH="/app/src"

EXPOSE 8088

ENTRYPOINT ["./entrypoint.sh"]
