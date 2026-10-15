# Supported Python versions are tested in .github/workflows/build.yml.
ARG PY_VERSION=3.13
FROM python:${PY_VERSION}-slim-bookworm AS builder

ARG FULL=false
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

# Copy only build inputs, never local .env files, credentials, or runtime data.
COPY pyproject.toml README.md LICENSE gpt3discord.py ./
COPY cogs ./cogs
COPY models ./models
COPY services ./services
COPY utils ./utils
COPY openers ./openers
COPY *_pretext*.txt conversation_drawing_ability*_snippet.txt ./

RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip \
    && if [ "$FULL" = "true" ]; then /opt/venv/bin/python -m pip install '.[full]'; \
       else /opt/venv/bin/python -m pip install .; fi \
    && /opt/venv/bin/python -m pip check

FROM python:${PY_VERSION}-slim-bookworm
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 gptdiscord \
    && useradd --uid 10001 --gid gptdiscord --no-create-home gptdiscord \
    && mkdir -p /data /opt/gpt3discord/bin /opt/gpt3discord/etc \
    && chown gptdiscord:gptdiscord /data
COPY --from=builder /opt/venv /opt/venv
# Preserve the existing /opt/gpt3discord/etc/environment mount location.
COPY --chmod=0644 gpt3discord.py /opt/gpt3discord/bin/gpt3discord.py
WORKDIR /data
USER gptdiscord
CMD ["python", "/opt/gpt3discord/bin/gpt3discord.py"]
