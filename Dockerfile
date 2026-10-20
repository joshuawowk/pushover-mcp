# Small, no-uv-at-runtime image. Tested install path: pip -> mcp 1.29.0 + httpx 0.28.1.
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/joshuawowk/pushover-mcp" \
      org.opencontainers.image.description="MCP server for the Pushover notification API" \
      org.opencontainers.image.licenses="MIT" \
      io.modelcontextprotocol.server.name="io.github.joshuawowk/pushover-mcp"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Pinned: MCP Python SDK 2.0 removed mcp.server.fastmcp.
RUN pip install --no-cache-dir "mcp>=1.2.0,<2.0" "httpx>=0.27"

COPY pushover_mcp.py /app/pushover_mcp.py

# Non-root.
RUN useradd --system --uid 10001 --no-create-home pushover
USER 10001

EXPOSE 8735

# GET /mcp returns 406 by design (streamable HTTP wants POST + SSE Accept),
# so health is a plain TCP connect.
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import socket,sys; s=socket.create_connection(('127.0.0.1',8735),3); s.close()" || exit 1

# No-args default is the HTTP server on 8735. Registry/stdio clients override CMD,
# e.g. `docker run -i <image> --stdio`.
ENTRYPOINT ["python", "/app/pushover_mcp.py"]
CMD ["--http", "--host", "0.0.0.0", "--port", "8735"]
