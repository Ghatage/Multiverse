#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin:/Applications/OrbStack.app/Contents/MacOS/xbin:$PATH"
runtime=auto
guest_platform=${FORK_PLATFORM:-linux/amd64}
build=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runtime)
      [ "$#" -ge 2 ] || { echo '--runtime needs auto, existing, orbstack, or docker-desktop' >&2; exit 2; }
      runtime=$2; shift 2 ;;
    --platform)
      [ "$#" -ge 2 ] || { echo '--platform needs linux/amd64 or linux/arm64' >&2; exit 2; }
      guest_platform=$2; shift 2 ;;
    --skip-build) build=0; shift ;;
    --help|-h)
      cat <<'HELP'
Usage: bash scripts/install.sh [--runtime auto|existing|orbstack|docker-desktop] [--platform linux/amd64|linux/arm64] [--skip-build]

Prepare macOS 14+ on Apple Silicon to run x86-64 Debian desktops.
Use --platform linux/arm64 for native ARM guests instead.
Requires uv 0.12.1+; missing or older uv is installed/upgraded.
Reuses the active Docker context; auto installs OrbStack only when no runtime exists.
Installs missing Homebrew/uv, Python 3.12, locked runtime dependencies, and local CA.
Builds fork-base:latest and fork-branch:latest unless --skip-build is given.
Existing .env, Docker settings, desktops, and checkpoints are preserved.
No model API requests are made. Run from a terminal for installer/first-launch prompts.
HELP
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$guest_platform" in linux/amd64|linux/arm64) ;; *) echo "Unsupported guest platform: $guest_platform" >&2; exit 2 ;; esac
case "$runtime" in auto|existing|orbstack|docker-desktop) ;; *) echo "Unsupported runtime: $runtime" >&2; exit 2 ;; esac
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  echo 'Run this installer in a native Apple Silicon macOS terminal. Default desktops run x86-64 Debian.' >&2
  exit 1
fi
macos_version=$(sw_vers -productVersion)
if [ "${macos_version%%.*}" -lt 14 ]; then
  echo 'macOS 14 or later is required by the supported Docker runtimes.' >&2
  exit 1
fi
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install || true
  echo 'Finish the Apple Command Line Tools installation dialog, then rerun this script.' >&2
  exit 1
fi
ensure_brew() {
  if ! command -v brew >/dev/null; then
    echo 'Installing Homebrew from its official installer; macOS may request administrator approval.'
    brew_installer=$(mktemp -t fork-homebrew)
    curl --fail --show-error --location https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$brew_installer"
    /bin/bash "$brew_installer"
    rm -f "$brew_installer"
  fi
  [ -x /opt/homebrew/bin/brew ] || { echo 'Apple Silicon Homebrew is required at /opt/homebrew/bin/brew.' >&2; exit 1; }
}
uv_ready() {
  command -v uv >/dev/null && uv --version | awk '{split($2,v,"."); exit !(v[1]>0 || v[2]>12 || (v[2]==12 && v[3]>=1))}'
}
if ! uv_ready; then
  ensure_brew
  if brew list --versions uv >/dev/null 2>&1; then brew upgrade uv; else brew install uv; fi
  hash -r
fi
uv_ready || { echo 'uv 0.12.1 or later is required.' >&2; exit 1; }
# A stopped existing runtime is started without switching its configured context.
if ! docker info >/dev/null 2>&1; then
  if [ "$runtime" = auto ]; then
    if [ -d /Applications/OrbStack.app ]; then runtime=orbstack
    elif [ -d /Applications/Docker.app ]; then runtime=docker-desktop
    elif command -v docker >/dev/null; then runtime=existing
    else runtime=orbstack; fi
  fi
  case "$runtime" in
    orbstack)
      if [ ! -d /Applications/OrbStack.app ]; then ensure_brew; brew install --cask orbstack; fi
      open -a OrbStack ;;
    docker-desktop)
      if [ ! -d /Applications/Docker.app ]; then ensure_brew; brew install --cask docker-desktop; fi
      open -a Docker ;;
    existing)
      echo 'Start the Docker runtime selected by docker context show, then rerun this installer.' >&2
      exit 1 ;;
  esac
  echo 'Waiting up to 120 seconds for Docker. Complete any first-launch prompts in the app.'
  for ((attempt=0; attempt<60; attempt++)); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
fi
docker info >/dev/null 2>&1 || { echo 'Docker is not ready. Start it, check docker context show, and rerun.' >&2; exit 1; }
docker buildx version >/dev/null || { echo 'Docker buildx is missing. Repair your Docker runtime installation and rerun.' >&2; exit 1; }
printf 'Using Docker context: %s\n' "$(docker context show)"
uv python install 3.12
uv sync --frozen --no-dev
if [ ! -e .env ]; then
  (umask 077; cp .env.example .env)
fi
bash scripts/setup-ca.sh
if [ "$build" -eq 1 ]; then
  FORK_PLATFORM="$guest_platform" make branch-build
  FORK_PLATFORM="$guest_platform" bash scripts/doctor.sh
fi
cat <<'DONE'
Setup complete. Start a desktop with:
  uv run --no-dev cu branch create work --json
  uv run --no-dev cu grid
To run the agent, set OPENAI_API_KEY in .env and provide a task JSON:
  uv run --no-dev fork-agent run --branch work --task /path/to/task.json
DONE
