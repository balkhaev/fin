FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

RUN python -m pip install --no-cache-dir . \
    && python scripts/build_frontend_data.py \
    && mkdir -p /data/runtime \
    && chown -R 10001:10001 /data

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
  CMD python scripts/check_paper_stack.py

CMD ["python", "scripts/run_paper_stack.py"]
