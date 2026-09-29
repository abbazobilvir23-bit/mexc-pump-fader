FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Сначала только метаданные и исходники — чтобы кэш слоёв работал.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[exchange]"

# Конфиги и скрипты (меняются чаще — отдельным слоем).
COPY config ./config
COPY scripts ./scripts

CMD ["mpf", "--help"]