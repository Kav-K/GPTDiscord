ARG PY_VERSION=3.9

# Build container
FROM python:${PY_VERSION} as base
FROM base as builder
ARG PY_VERSION

RUN mkdir /install /src
WORKDIR /install
RUN pip install --target="/install" --upgrade pip setuptools wheel
COPY requirements.txt /install
RUN pip install --target="/install" -r requirements.txt
COPY README.md /src
COPY cogs /src/cogs
COPY models /src/models
COPY services /src/services
COPY gpt3discord.py /src
COPY pyproject.toml /src
# For debugging + seeing that the modiles file layouts look correct ...
RUN find /src
RUN pip install --target="/install" /src

# Copy minimal to main image (to keep as small as possible)
FROM python:${PY_VERSION}-slim
ARG PY_VERSION
COPY --from=builder /install /usr/local/lib/python${PY_VERSION}/site-packages
RUN mkdir -p /opt/gpt3discord/etc
COPY gpt3discord.py /opt/gpt3discord/bin/
COPY image_optimizer_pretext.txt conversation_starter_pretext.txt conversation_starter_pretext_minimal.txt /opt/gpt3discord/share/
COPY openers /opt/gpt3discord/share/openers
CMD ["python3", "/opt/gpt3discord/bin/gpt3discord.py"]
