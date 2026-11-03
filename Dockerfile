FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts
RUN pip install . && playwright install --with-deps chromium

EXPOSE 8080
CMD ["python", "-m", "src.run"]
