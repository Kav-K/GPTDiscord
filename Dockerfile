ARG PY_VERSION=3.9

FROM python:${PY_VERSION}-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    curl

RUN curl https://sh.rustup.rs -sSf | bash -s -- -y \
    && export PATH="/root/.cargo/bin:${PATH}" \
    && pip install --upgrade pip setuptools wheel setuptools_rust \
    && apt-get clean

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app
WORKDIR /app

CMD ["python", "gpt3discord.py"]