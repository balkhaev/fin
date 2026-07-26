FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system router && useradd --system --gid router --create-home router
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --upgrade pip && python -m pip install .

RUN mkdir -p /app/data && chown -R router:router /app
USER router

ENTRYPOINT ["funding-router"]
CMD ["--config", "/app/config.toml", "paper"]
