FROM n8nio/n8n:latest

USER root

# Python 및 필수 패키지 설치 (Debian 기반)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지 설치
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

# n8n 기본 포트
EXPOSE 5678

# 시작 명령
CMD ["n8n", "start"]
