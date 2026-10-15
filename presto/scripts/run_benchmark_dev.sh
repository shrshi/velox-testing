#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION.
# SPDX-License-Identifier: Apache-2.0

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Use the standard benchmark lifecycle with a persistent developer environment.
export PRESTO_BENCHMARK_DEV_VENV="${PRESTO_BENCHMARK_DEV_VENV:-${SCRIPT_DIR}/.venv-dev}"
exec "${SCRIPT_DIR}/run_benchmark.sh" "$@"
