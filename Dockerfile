FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts
COPY sandbox ./sandbox
COPY evals ./evals
COPY tests/fixtures ./tests/fixtures
COPY tests/sandbox_escape_probe.py ./tests/
RUN pip install . && playwright install --with-deps chromium

EXPOSE 8080
CMD ["python", "-m", "src.run"]
