# Playwright's official image ships Chromium and every system library it needs. The tag must
# match the playwright version pinned in pyproject.toml (browser builds are version-locked).
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORK_DIR=/data/out \
    HEADLESS=true \
    BROWSER_CHANNEL=chromium

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && mkdir -p /data/out \
    && chown -R pwuser:pwuser /app /data

USER pwuser
VOLUME ["/data"]

ENTRYPOINT ["uarb-agent"]
CMD ["serve"]
