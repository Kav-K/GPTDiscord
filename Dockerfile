ARG PY_VERSION=3.10

# Build container
FROM python:${PY_VERSION} as base
FROM base as builder
ARG PY_VERSION
ARG TARGETPLATFORM
ARG FULL

COPY . .

#Install rust
RUN apt-get update
RUN apt-get install -y \
    build-essential \
    gcc \
    curl
RUN curl https://sh.rustup.rs -sSf | bash -s -- -y
ARG PATH="/root/.cargo/bin:${PATH}"
# https://github.com/rust-lang/cargo/issues/10583
ARG CARGO_NET_GIT_FETCH_WITH_CLI=true

RUN mkdir /install /src
WORKDIR /install

RUN pip install --target="/install" --upgrade pip setuptools wheel setuptools_rust

COPY requirements_base.txt /install
COPY requirements_full.txt /install
RUN pip install --target="/install" --upgrade -r requirements_base.txt
RUN if [ "${FULL}" = "true" ]; then \
    if [ -z "{$TARGETPLATFORM}" ]; then pip install --target="/install" --upgrade torch==1.9.1+cpu torchvision==0.10.1+cpu -f https://download.pytorch.org/whl/torch_stable.html ; fi \
    ; if [ "${TARGETPLATFORM}" = "linux/amd64" ]; then pip install --target="/install" --upgrade torch==1.9.1+cpu torchvision==0.10.1+cpu -f https://download.pytorch.org/whl/torch_stable.html ; fi \
    ; if [ "${TARGETPLATFORM}" = "linux/arm64" ]; then pip install --target="/install" --upgrade torch==1.9.0 torchvision==0.10.0 -f https://torch.kmtea.eu/whl/stable.html -f https://ext.kmtea.eu/whl/stable.html ; fi \  
    ; pip install --target="/install" --upgrade \
       -r requirements_full.txt \
    ; pip install --target="/install" --upgrade \
       --no-deps --no-build-isolation openai-whisper sentence-transformers==2.2.2 \
    ; fi

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
COPY . .
COPY --from=builder /install /usr/local/lib/python${PY_VERSION}/site-packages
#Install ffmpeg and clean
RUN apt-get -y update
RUN apt-get -y install --no-install-recommends ffmpeg
RUN apt-get clean
RUN rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/gpt3discord/etc
COPY gpt3discord.py /opt/gpt3discord/bin/
COPY image_optimizer_pretext.txt language_detection_pretext.txt conversation_starter_pretext.txt conversation_starter_pretext_minimal.txt /opt/gpt3discord/share/
COPY openers /opt/gpt3discord/share/openers
CMD ["python3", "/opt/gpt3discord/bin/gpt3discord.py"]
