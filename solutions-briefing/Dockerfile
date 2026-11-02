# One image, three Fly process groups (web / worker / scheduler) — see fly.toml.
# glibc base (Debian slim) is required: claude-agent-sdk ships a bundled native Claude Code binary.
# Pulled from the AWS ECR mirror of Docker Official Images to avoid Docker Hub anonymous rate limits.
FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Same uv version that wrote uv.lock.
RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# Dependencies first (cached layer), from the lockfile only.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra agents --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra agents

RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

EXPOSE 8080
CMD ["uvicorn", "solbrief.web.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
