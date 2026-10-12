#!/bin/bash
set -Eeuo pipefail

# Launch one of the versioned Velox devcontainer configurations.
PROJECT_DIR=$(pwd)
DEVCONTAINER_NAMES=()
DEVCONTAINER_CONFIGS=()

for config in "$PROJECT_DIR"/.devcontainer/*/devcontainer.json; do
  [ -f "$config" ] || continue
  DEVCONTAINER_NAMES+=("$(basename "$(dirname "$config")")")
  DEVCONTAINER_CONFIGS+=("$config")
done

if [ "${#DEVCONTAINER_NAMES[@]}" -eq 0 ]; then
  echo "No devcontainer configurations found under $PROJECT_DIR/.devcontainer" >&2
  exit 1
fi

choose_devcontainer() {
  echo "Select a devcontainer (or provide its name as an argument):" >&2
  select name in "${DEVCONTAINER_NAMES[@]}"; do
    if [ "$REPLY" = "q" ] || [ "$REPLY" = "quit" ] || [ "$REPLY" = "exit" ]; then
      return 1
    fi
    if [ "$REPLY" -ge 1 ] 2>/dev/null && [ "$REPLY" -le "${#DEVCONTAINER_NAMES[@]}" ]; then
      printf '%s\n' "${DEVCONTAINER_CONFIGS[$((REPLY - 1))]}"
      return
    fi
    echo "Invalid choice: select a number from 1 to ${#DEVCONTAINER_NAMES[@]}" >&2
  done
}

select_devcontainer() {
  if [ "$#" -eq 0 ]; then
    if ! choose_devcontainer; then
      echo "Devcontainer selection cancelled" >&2
      return 1
    fi
    return
  fi

  if [ "$#" -ne 1 ]; then
    echo "Usage: $0 [devcontainer-name]" >&2
    exit 1
  fi

  for i in "${!DEVCONTAINER_NAMES[@]}"; do
    if [ "${DEVCONTAINER_NAMES[$i]}" = "$1" ]; then
      printf '%s\n' "${DEVCONTAINER_CONFIGS[$i]}"
      return
    fi
  done

  echo "Unknown devcontainer: $1" >&2
  echo "Available devcontainers: ${DEVCONTAINER_NAMES[*]}" >&2
  exit 1
}

if ! DEVCONTAINER_CONFIG=$(select_devcontainer "$@"); then
  exit 1
fi
if [ -z "${DEVCONTAINER_CONFIG:-}" ] || [ ! -f "$DEVCONTAINER_CONFIG" ]; then
  echo "No valid devcontainer configuration was selected" >&2
  exit 1
fi

# Prefer an explicit key, then any Git scope visible to this repository.
# Deliberately do not infer a key from the keyring: signing must be explicitly
# configured by the host user.
GPG_SIGNING_KEY=${GPG_SIGNING_KEY:-$(git config --get user.signingkey || true)}
gpgconf --launch gpg-agent
export GPG_AGENT_EXTRA_SOCK=${GPG_AGENT_EXTRA_SOCK:-$(gpgconf --list-dirs agent-extra-socket)}
GPG_PUBLIC_KEY_FILE="$HOME/.config/devcontainer/git-signing-public-key.asc"

if [ -z "${SSH_AUTH_SOCK:-}" ] || [ ! -S "${SSH_AUTH_SOCK:-}" ]; then
  echo "SSH_AUTH_SOCK does not point to a running SSH agent" >&2
  exit 1
fi
if [ ! -S "$GPG_AGENT_EXTRA_SOCK" ]; then
  echo "GPG agent socket not found: $GPG_AGENT_EXTRA_SOCK" >&2
  exit 1
fi
if [ -z "$GPG_SIGNING_KEY" ]; then
  echo "Set GPG_SIGNING_KEY or configure git user.signingkey on the host" >&2
  exit 1
fi

mkdir -p "$(dirname "$GPG_PUBLIC_KEY_FILE")"
if ! gpg --armor --export "$GPG_SIGNING_KEY" >"$GPG_PUBLIC_KEY_FILE"; then
  echo "Could not export GPG signing key: $GPG_SIGNING_KEY" >&2
  exit 1
fi
if [ ! -s "$GPG_PUBLIC_KEY_FILE" ]; then
  echo "Could not export GPG signing key: $GPG_SIGNING_KEY" >&2
  exit 1
fi

echo "Building DevContainer from $DEVCONTAINER_CONFIG..."
devcontainer up --config "$DEVCONTAINER_CONFIG" --workspace-folder "$PROJECT_DIR"

echo "Starting container..."
devcontainer exec --config "$DEVCONTAINER_CONFIG" --workspace-folder "$PROJECT_DIR" bash

echo "Container stopped."
