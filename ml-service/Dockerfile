FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/root/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/root/.cache/huggingface/sentence_transformers

ARG INSTALL_DENSE=1

# curl is used by the inline Docker Compose startup/readiness command.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dense.txt ./

RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_DENSE" = "1" ]; then \
         pip install --no-cache-dir -r requirements-dense.txt; \
       fi

COPY app ./app
COPY pipeline ./pipeline
COPY scripts ./scripts

EXPOSE 9000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
