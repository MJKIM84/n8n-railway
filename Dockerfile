FROM python:3.11-slim AS python-base

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

COPY --from=python-base /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=python-base /usr/local/bin/python3.11 /usr/local/bin/python3.11
COPY --from=python-base /usr/local/bin/python3 /usr/local/bin/python3

RUN ln -sf /usr/local/bin/python3.11 /usr/bin/python3

USER node

EXPOSE 5678

CMD ["n8n", "start"]
