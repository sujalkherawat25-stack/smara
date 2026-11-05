FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Dependencies and Chromium first: these layers are cached until pyproject.toml changes,
# so a code-only release rebuilds in seconds instead of re-downloading ~1 GB.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/req.txt \
 && pip install -r /tmp/req.txt \
 && playwright install --with-deps chromium

COPY src ./src
COPY bridge ./bridge
COPY migrations ./migrations
COPY scripts ./scripts
COPY sandbox ./sandbox
COPY evals ./evals
COPY tests/fixtures ./tests/fixtures
COPY tests/sandbox_escape_probe.py ./tests/
RUN pip install --no-deps .

EXPOSE 8080
CMD ["python", "-m", "src.run"]
