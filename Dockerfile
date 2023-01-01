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
COPY gpt3discord.py /src
COPY pyproject.toml /src
# For debugging + seeing that the modiles file layouts look correct ...
find /src
RUN pip install --target="/install" /src

# Copy minimal to main image (to keep as small as possible)
FROM python:${PY_VERSION}-slim
ARG PY_VERSION
COPY --from=builder /install /usr/local/lib/python${PY_VERSION}/site-packages
COPY gpt3discord.py /bin/gpt3discord
CMD ["python3", "/bin/gpt3discord"]
