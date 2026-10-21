# The whole studio in one image: the web app built, then served by the Python API.

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
# the Speech SDK needs OpenSSL, CA certificates and ALSA's shared library, even with no sound card
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libssl3 libasound2 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY rundown/ rundown/
COPY schema/ schema/
RUN pip install --no-cache-dir ".[api,agents]"
COPY --from=web /web/dist web/dist
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "rundown.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
