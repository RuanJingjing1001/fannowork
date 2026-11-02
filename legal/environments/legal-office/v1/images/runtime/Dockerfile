FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/tmp/fanno-home

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libreoffice-core \
        libreoffice-writer \
        libreoffice-script-provider-python \
        libreoffice-calc \
        nodejs \
        npm \
        python3-uno \
        ripgrep \
        fonts-dejavu-core \
        fonts-liberation \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Harbor's installed Codex adapter skips its network bootstrap when a
# working Codex CLI is already present. Pin the version so task startup is
# reproducible and does not require Debian, GitHub, Node.js, or npm access.
ARG CODEX_VERSION=0.148.0
RUN npm install --global "@openai/codex@${CODEX_VERSION}" \
    && codex --version

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

COPY legal ./legal

RUN pip install --no-cache-dir ./legal

RUN mkdir -p /opt/fanno/workers

COPY src/fanno_work/sandbox/filesystem_worker.py \
    /opt/fanno/workers/filesystem_worker.py

COPY legal/src/fanno_legal/backends/document/uno_worker.py \
    /opt/fanno/workers/uno_worker.py

COPY legal/src/fanno_legal/backends/document/document_worker.py \
    /opt/fanno/workers/document_worker.py

COPY legal/src/fanno_legal/backends/spreadsheet/calc_worker.py \
    /opt/fanno/workers/calc_worker.py

COPY legal/src/fanno_legal/backends/email/email_worker.py \
    /opt/fanno/workers/email_worker.py

COPY legal/src/fanno_legal/backends/calendar/calendar_worker.py \
    /opt/fanno/workers/calendar_worker.py
    
RUN useradd \
    --create-home \
    --uid 10001 \
    fanno

RUN mkdir -p /tmp/fanno-home \
    && chown -R fanno:fanno \
        /tmp/fanno-home \
        /app

USER fanno

CMD ["python", "-m", "unittest", "discover", "-s", "legal/tests", "-v"]
