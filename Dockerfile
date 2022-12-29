ARG PY_VERSION=3.9

FROM python:${PY_VERSION} as base

FROM base as builder
ARG PY_VERSION

RUN mkdir /install
WORKDIR /install
RUN pip install --target="/install" --upgrade pip setuptools wheel
ADD requirements.txt /install
RUN pip install --target="/install" -r requirements.txt


FROM python:${PY_VERSION}-slim

ARG PY_VERSION

COPY --from=builder /install /usr/local/lib/python${PY_VERSION}/site-packages
COPY cogs /usr/local/lib/python${PY_VERSION}/site-packages/cogs
COPY models /usr/local/lib/python${PY_VERSION}/site-packages/models
COPY main.py /bin/gpt3discord

CMD ["python3", "/bin/gpt3discord"]
