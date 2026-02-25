FROM n8nio/n8n:latest

USER root

# Python 및 필수 패키지 설치
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-numpy \
    py3-pandas \
    gcc \
    musl-dev \
    python3-dev \
    libffi-dev

# Python 패키지 설치
RUN pip3 install --break-system-packages \
    pykrx \
    finance-datareader \
    yfinance \
    mplfinance \
    matplotlib \
    requests

USER node

# n8n 기본 포트
EXPOSE 5678

# 시작 명령
CMD ["n8n", "start"]
