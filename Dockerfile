FROM python:3.11-slim AS python-builder

RUN pip install --no-cache-dir \
    pykrx \
    finance-datareader \
    yfinance \
    mplfinance \
    matplotlib \
    pandas \
    numpy \
    requests

FROM n8nio/n8n:latest

USER root

# Python 바이너리와 라이브러리 복사
COPY --from=python-builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=python-builder /usr/local/bin/python3.11 /usr/local/bin/python3.11
COPY --from=python-builder /usr/local/lib/libpython3.11.so* /usr/local/lib/
COPY --from=python-builder /usr/local/include/python3.11 /usr/local/include/python3.11

RUN ln -sf /usr/local/bin/python3.11 /usr/local/bin/python3 && \
    ln -sf /usr/local/bin/p
