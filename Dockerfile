# syntax=docker/dockerfile:1

# ---- builder: install dependencies into a virtualenv ----------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only what dependency resolution needs first, so this layer caches until
# pyproject.toml changes rather than on every source edit.
COPY backend/pyproject.toml backend/README.md ./backend/
COPY backend/faultline ./backend/faultline
RUN pip install ./backend

# ---- test: the same image plus dev tooling; CI runs the unit suite in here -------
FROM builder AS test
WORKDIR /app/backend
COPY backend/ ./
RUN pip install ".[dev]"
CMD ["pytest", "tests/unit", "-q"]

# ---- production: slim runtime, no compilers, non-root -----------------------------
FROM python:3.12-slim AS production

# git is needed for cloning repositories by URL; nothing else is.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 faultline

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

USER faultline
WORKDIR /home/faultline
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "faultline.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
