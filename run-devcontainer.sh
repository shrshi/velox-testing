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

# Git commits are signed with an SSH key held by the host SSH agent; the
# private key never enters the container.
SSH_SIGNING_KEY=${SSH_SIGNING_KEY:-$HOME/.ssh/github-signing}
SSH_PUBLIC_KEY_FILE="$HOME/.config/devcontainer/git-signing-key.pub"

if [ -z "${SSH_AUTH_SOCK:-}" ] || [ ! -S "${SSH_AUTH_SOCK:-}" ]; then
  echo "SSH_AUTH_SOCK does not point to a running SSH agent" >&2
  exit 1
fi
if [ ! -f "$SSH_SIGNING_KEY.pub" ]; then
  echo "SSH signing public key not found: $SSH_SIGNING_KEY.pub" >&2
  exit 1
fi

mkdir -p "$(dirname "$SSH_PUBLIC_KEY_FILE")"
cp "$SSH_SIGNING_KEY.pub" "$SSH_PUBLIC_KEY_FILE"
if ! ssh-add -L | grep -qF "$(cut -d' ' -f2 "$SSH_PUBLIC_KEY_FILE")"; then
  ssh-add "$SSH_SIGNING_KEY"
fi

echo "Building DevContainer from $DEVCONTAINER_CONFIG..."
devcontainer up --config "$DEVCONTAINER_CONFIG" --workspace-folder "$PROJECT_DIR"

echo "Starting container..."
devcontainer exec --config "$DEVCONTAINER_CONFIG" --workspace-folder "$PROJECT_DIR" bash

echo "Container stopped."
