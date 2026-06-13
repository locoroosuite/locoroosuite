#!/bin/bash
set -e

mkdir -p /app/data/caches /app/data/logs /app/data/import_uploads

if [ ! -f /app/data/snippet_patterns.json ]; then
    cp /defaults/snippet_patterns.json /app/data/snippet_patterns.json
fi

if [ "${MCP_ENABLED:-true}" = "true" ]; then
    echo "Starting MCP server on port 8001..."
    nohup python -m app.mcp.server > /app/data/logs/mcp.log 2>&1 &
    echo "MCP server started (PID $!)"
fi

exec "$@"
