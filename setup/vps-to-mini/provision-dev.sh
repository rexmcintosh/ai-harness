#!/usr/bin/env bash
# Toolchain for user `dev` in the VM. Run as dev. Idempotent. Versions match the VPS where it matters.
set -euo pipefail
cd "$HOME"
NODE_VERSION=22.22.3

if [ ! -s "$HOME/.nvm/nvm.sh" ]; then
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | PROFILE=/dev/null bash
fi
set +u; . "$HOME/.nvm/nvm.sh"; nvm install "$NODE_VERSION" >/dev/null; nvm alias default "$NODE_VERSION" >/dev/null; set -u

command -v bun >/dev/null || [ -x "$HOME/.bun/bin/bun" ] || curl -fsSL https://bun.sh/install | bash >/dev/null
[ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
"$HOME/.local/bin/uv" python install 3.11 >/dev/null

[ -x "$HOME/.local/bin/claude" ] || curl -fsSL https://claude.ai/install.sh | bash >/dev/null
npm ls -g @openai/codex >/dev/null 2>&1 || npm install -g @openai/codex >/dev/null

# the VPS crontab and session-bridge unit call /usr/local/bin/bun
[ -x /usr/local/bin/bun ] || sudo ln -sf "$HOME/.bun/bin/bun" /usr/local/bin/bun

echo "DEV-OK node=$(node -v) bun=$("$HOME/.bun/bin/bun" -v) uv=$("$HOME/.local/bin/uv" --version | cut -d' ' -f2) claude=$("$HOME/.local/bin/claude" --version 2>/dev/null | head -1) codex=$(codex --version 2>/dev/null | head -1)"
