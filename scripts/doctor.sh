#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin:/Applications/OrbStack.app/Contents/MacOS/xbin:$PATH"
fail=0
check() { if "$@" >/dev/null 2>&1; then printf 'OK: %s\n' "$*"; else printf 'MISSING: %s\n' "$*"; fail=1; fi; }
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  echo 'This host installer supports Apple Silicon macOS. Guest desktops use Debian ARM64.' >&2
  exit 1
fi
check command -v uv
check command -v docker
check docker info
check docker buildx version
if command -v uv >/dev/null; then
  check uv run --frozen --no-dev python -c 'import sys; assert sys.version_info[:2] == (3, 12); import fork.cu.cli, fork.agent.cli'
fi
if docker info >/dev/null 2>&1; then
  printf 'Docker context: %s\n' "$(docker context show)"
  printf 'Docker VM memory (bytes): %s\n' "$(docker info --format '{{.MemTotal}}')"
fi
check test -s env/proxy/mitmproxy-ca-cert.pem
check docker image inspect fork-base:latest
check docker image inspect fork-branch:latest
printf 'API credentials are optional for desktop setup; configure OPENAI_API_KEY in .env to run the agent.\n'
exit "$fail"
