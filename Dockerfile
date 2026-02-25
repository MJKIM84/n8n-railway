FROM n8nio/n8n:latest

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages \
    pykrx \
    finance-datareader \
    yfinance \
    mplfinance \
    matplotlib \
    pandas \
    numpy \
    requests

USER node

EXPOSE 5678

CMD ["n8n", "start"]
