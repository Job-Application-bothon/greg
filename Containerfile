FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Copy project metadata and source
COPY pyproject.toml uv.lock .python-version README.md ./
COPY src/ ./src/

# Install runtime dependencies (including planner extra) into /app/.venv
RUN uv sync --frozen --no-dev --extra planner

# Verify planner.keras NumPy runner and statsforecast load cleanly inside the image
RUN /app/.venv/bin/python -c "from greg.forecasting.seasonal import get_planner; from statsforecast.models import AutoETS, AutoARIMA, AutoTheta; assert get_planner().model is not None"

# OpenShift runs containers with an arbitrary UID in gid 0 (root group).
# Ensure /app is readable and group-executable/writable for gid 0.
RUN chgrp -R 0 /app && chmod -R g=u /app

USER 1001

EXPOSE 8000

CMD ["/app/.venv/bin/greg"]
