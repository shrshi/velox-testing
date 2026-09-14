#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Shared constants and functions for velox/presto build scripts.
# Source this file; do not execute directly.

# Resolve CUDAARCHS to the architecture of the GPU visible in this container.
resolve_cudaarchs() {
    if [[ -z "${CUDAARCHS:-}" || "${CUDAARCHS}" == "native" ]]; then
        export CUDAARCHS="native"
    fi
}

select_sccache_family() {
    local family="$1"

    case "${family}" in
        rapids)  export SCCACHE_SERVER_PORT=4227 ;;
        velox)   export SCCACHE_SERVER_PORT=4228 ;;
        presto)  export SCCACHE_SERVER_PORT=4229 ;;
        fb-deps) export SCCACHE_SERVER_PORT=4230 ;;
        *)
            echo "Error: unknown sccache family: ${family}" >&2
            return 1
            ;;
    esac

    export SCCACHE_DIR="/tmp/sccache/${family}"
    mkdir -p "${SCCACHE_DIR}"

    export SCCACHE_DIST_FALLBACK_TO_LOCAL_COMPILE=false
    export SCCACHE_DIST_MAX_RETRIES="${SCCACHE_DIST_MAX_RETRIES:-4}"
    unset SCCACHE_NO_DIST_COMPILE
    export CMAKE_C_COMPILER_LAUNCHER=sccache
    export CMAKE_CXX_COMPILER_LAUNCHER=sccache
    export CMAKE_CUDA_COMPILER_LAUNCHER=sccache
}

require_remote_sccache() {
    command -v sccache >/dev/null || {
        echo "Error: sccache is required" >&2
        return 1
    }
    command -v jq >/dev/null || {
        echo "Error: jq is required to validate sccache-dist" >&2
        return 1
    }
    [[ -n "${SCCACHE_SERVER_PORT:-}" && -n "${SCCACHE_DIR:-}" ]] || {
        echo "Error: call select_sccache_family before require_remote_sccache" >&2
        return 1
    }

    if ! sccache --show-stats >/dev/null 2>&1; then
        sccache --start-server >/dev/null
    fi
    sccache --dist-auth >/dev/null

    local status
    status=$(sccache --dist-status --stats-format json)
    if ! jq -e '(.SchedulerStatus[1].servers // []) | length > 0' \
        <<<"${status}" >/dev/null; then
        echo "Error: sccache-dist scheduler reports no build servers" >&2
        return 1
    fi
}

validate_remote_sccache_stats() {
    local stats
    stats=$(sccache --show-adv-stats --stats-format json)
    if ! jq -e '
        .stats.requests_not_cacheable == 0 and
        .stats.requests_unsupported_compiler == 0 and
        .stats.non_cacheable_compilations == 0 and
        .stats.dist_errors == 0
    ' <<<"${stats}" >/dev/null; then
        echo "Error: compilation did not remain fully remote through sccache" >&2
        jq '.stats | {
            requests_not_cacheable,
            requests_unsupported_compiler,
            non_cacheable_compilations,
            dist_errors
        }' <<<"${stats}" >&2
        return 1
    fi
}

restart_remote_sccache() {
    sccache --stop-server >/dev/null 2>&1 || true
    sccache --start-server >/dev/null
    require_remote_sccache
}

# Look up the cmake build directory for a RAPIDS library (rmm, ucxx, kvikio, rapidsmpf, cudf).
# Prints the path if found; prints nothing otherwise.
get_rapids_cmake_dir() {
    local lib_name="$1"
    local repo_path="${HOME}/${lib_name}"

    if command -v rapids-get-cmake-build-dir &>/dev/null && [[ -d "${repo_path}" ]]; then
        local build_dir
        build_dir=$(rapids-get-cmake-build-dir "${repo_path}/cpp" 2>/dev/null || true)
        if [[ -n "${build_dir}" && -d "${build_dir}" ]]; then
            echo "${build_dir}"
        fi
    fi
}

# Append -D flags for pre-built rmm, ucxx, kvikio, rapidsmpf, cudf to the variable whose
# name is passed as $1 (must already exist in the caller's scope).
append_rapids_cmake_flags() {
    local __var="$1"

    local rmm_dir ucxx_dir kvikio_dir rapidsmpf_dir cudf_dir
    rmm_dir=$(get_rapids_cmake_dir "rmm")
    ucxx_dir=$(get_rapids_cmake_dir "ucxx")
    kvikio_dir=$(get_rapids_cmake_dir "kvikio")
    rapidsmpf_dir=$(get_rapids_cmake_dir "rapidsmpf")
    cudf_dir=$(get_rapids_cmake_dir "cudf")

    if [[ -n "${rmm_dir}" ]]; then
        echo "Found rmm build at: ${rmm_dir}"
        printf -v "$__var" '%s %s' "${!__var}" "-Drmm_ROOT=${rmm_dir}"
    fi
    if [[ -n "${ucxx_dir}" ]]; then
        echo "Found ucxx build at: ${ucxx_dir}"
        printf -v "$__var" '%s %s' "${!__var}" "-Ducxx_ROOT=${ucxx_dir}"
    fi
    if [[ -n "${kvikio_dir}" ]]; then
        echo "Found kvikio build at: ${kvikio_dir}"
        printf -v "$__var" '%s %s' "${!__var}" "-Dkvikio_ROOT=${kvikio_dir}"
    fi
    if [[ -n "${rapidsmpf_dir}" ]]; then
        echo "Found rapidsmpf build at: ${rapidsmpf_dir}"
        printf -v "$__var" '%s %s' "${!__var}" "-Drapidsmpf_ROOT=${rapidsmpf_dir}"
    fi
    if [[ -n "${cudf_dir}" ]]; then
        echo "Found cudf build at: ${cudf_dir}"
        printf -v "$__var" '%s %s' "${!__var}" "-Dcudf_ROOT=${cudf_dir}"
    fi
}
