FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Dependencies and Chromium first: these layers are cached until pyproject.toml changes,
# so a code-only release rebuilds in seconds instead of re-downloading ~1 GB.
COPY pyproject.toml ./
# libatomic1: the Playwright driver needs it on the current python:3.12-slim base; without it the build fails.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/* \
 && python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/req.txt \
 && pip install -r /tmp/req.txt \
 && playwright install --with-deps chromium

# A virtual screen, so the server runs a normal headed Chromium instead of a headless one (own layer: keeps the cache above).
RUN apt-get update && apt-get install -y --no-install-recommends xvfb && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY bridge ./bridge
COPY migrations ./migrations
COPY scripts ./scripts
COPY sandbox ./sandbox
COPY skills ./skills
COPY assets ./assets
COPY evals ./evals
COPY tests/fixtures ./tests/fixtures
COPY tests/sandbox_escape_probe.py ./tests/
RUN pip install --no-deps .

EXPOSE 8080
CMD ["python", "-m", "src.run"]
