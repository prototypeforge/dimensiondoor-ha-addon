ARG BUILD_FROM
FROM ${BUILD_FROM:-python:3.12-slim}

# Install Python and dependencies on Alpine-based HA images
RUN apk add --no-cache python3 py3-pip bash || true

RUN pip3 install --no-cache-dir --break-system-packages \
    websockets==13.1 \
    aiohttp==3.11.11 \
    msgpack==1.1.0 \
    pyyaml==6.0.2

# Copy application
COPY tunnel_client.py /app/tunnel_client.py
COPY configure_ha.py /app/configure_ha.py
COPY run.sh /app/run.sh
RUN chmod a+x /app/run.sh

WORKDIR /app

CMD ["/app/run.sh"]
