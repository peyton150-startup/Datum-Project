FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY datum ./datum
RUN python -m pip install -e ".[dev]"
COPY . .
CMD ["gunicorn", "datum.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
