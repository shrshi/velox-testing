#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION.
# SPDX-License-Identifier: Apache-2.0

# Strip any userinfo (e.g. "user:token@" or "token@") from an http(s) remote URL so
# credentials embedded in the remote are never persisted in provenance labels, the
# baked provenance.json, shared logs, or benchmark results. SSH URLs are left as-is.
function _strip_url_credentials() {
  printf '%s' "$1" | sed -E 's#^(https?://)[^@/]*@#\1#'
}

# Resolve before scripts change directory; Docker Compose needs an absolute path.
function resolve_velox_source_dir() {
  VELOX_SOURCE_DIR="$(realpath -m "${VELOX_SOURCE_DIR:-$1/../velox}")" || return
  export VELOX_SOURCE_DIR
}

# Sets PRESTO_SHA, PRESTO_BRANCH, PRESTO_REPO, VELOX_SHA, VELOX_BRANCH, VELOX_REPO
# from sibling Presto and the selected Velox checkout (sibling Velox by default).
function capture_build_provenance() {
  local repo_root="$1"
  local velox_source_dir="${VELOX_SOURCE_DIR:-${repo_root}/../velox}"
  PRESTO_SHA=$(git -C "${repo_root}/../presto" rev-parse HEAD 2>/dev/null || echo "")
  PRESTO_BRANCH=$(git -C "${repo_root}/../presto" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  PRESTO_REPO=$(_strip_url_credentials "$(git -C "${repo_root}/../presto" remote get-url origin 2>/dev/null || echo "")")
  VELOX_SHA=$(git -C "${velox_source_dir}" rev-parse HEAD 2>/dev/null || echo "")
  VELOX_BRANCH=$(git -C "${velox_source_dir}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  VELOX_REPO=$(_strip_url_credentials "$(git -C "${velox_source_dir}" remote get-url origin 2>/dev/null || echo "")")
}

function wait_for_worker_node_registration() {
  local host="$1"
  local port="$2"

  if [[ -z "${host}" || -z "${port}" ]]; then
    echo "Error: wait_for_worker_node_registration requires hostname and port arguments."
    exit 1
  fi

  trap "rm -rf node_response.json" RETURN

  echo "Waiting for a worker node to be registered..."
  COORDINATOR_URL=http://${host}:${port}
  echo "Coordinator URL: $COORDINATOR_URL"
  local -r MAX_RETRIES=12
  local retry_count=0
  until curl -s -f -o node_response.json ${COORDINATOR_URL}/v1/node && \
        (( $(jq length node_response.json) > 0 )); do
    if (( $retry_count >= $MAX_RETRIES )); then
      echo "Error: Worker node not registered after 60s. Exiting."
      exit 1
    fi
    sleep 5
    retry_count=$(( retry_count + 1 ))
  done
  echo "Worker node registered"
}
