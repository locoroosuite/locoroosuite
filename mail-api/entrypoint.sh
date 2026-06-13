#!/bin/bash
set -e

python policy_server.py &
POLICY_PID=$!

exec python server.py
