FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY web ./web
COPY db ./db
COPY config ./config
COPY scripts ./scripts

RUN pip install --no-cache-dir .

ENV OSS_MENTOR_HOST=0.0.0.0
ENV OSS_MENTOR_PORT=8765

EXPOSE 8765

CMD ["python", "-m", "oss_mentor", "serve-api"]
