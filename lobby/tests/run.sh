#!/usr/bin/env bash
# The lobby's tests from outside (test_lobby.py): builds it (Docker, ../cargo), then runs them in a Python container
# against the binary, on that container's loopback. Nothing to install here.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
"$here/../cargo" build -p lobby
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v visit-target:/target:ro -v "$repo":/repo:ro -w /repo/lobby/tests python:3.13-slim-bookworm \
  sh -c 'pip install -q --user -r requirements.txt 2>/dev/null && python3 -u test_lobby.py /target/debug/lobby'
