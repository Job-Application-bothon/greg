FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Copy project metadata and source
COPY pyproject.toml .python-version ./
COPY src/ ./src/

# Install runtime dependencies into /app/.venv
# Once uv.lock is committed, switch to: uv sync --frozen --no-dev
RUN uv sync --no-dev

# OpenShift runs containers with an arbitrary UID in gid 0 (root group).
# Ensure /app is readable and group-executable/writable for gid 0.
RUN chgrp -R 0 /app && chmod -R g=u /app

USER 1001

EXPOSE 8000

CMD ["/app/.venv/bin/greg"]
