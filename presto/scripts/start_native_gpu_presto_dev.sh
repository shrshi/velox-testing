#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION.
# SPDX-License-Identifier: Apache-2.0

set -eo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

print_dev_help() {
  cat <<EOF
Usage: $0 [DEV_OPTIONS] [START_OPTIONS]

Build selected sibling sources with persistent native object and Maven caches.
Runtime defaults, profiling, CTAS mounts and GPU NUMA policy come from main's
launch templates; this script does not apply an execution tuning profile.

DEV_OPTIONS:
  --presto-source PATH       Presto checkout (default: ../presto).
  --velox-source PATH        Velox checkout (default: ../velox).
  --restart-target TARGET   all (default), worker, coordinator, or none.
  --clean-first             Stop existing Presto containers before building.
                            Requires --restart-target all.
  --clean-java              Run Maven clean; otherwise build incrementally.
  --wait                    Wait for healthy, coordinator-ACTIVE GPU workers.
  --wait-timeout SECONDS     Readiness deadline (default: 180).

START_OPTIONS:
  --build all|worker|coordinator (also -b); missing images are built lazily.
  --build-type release|relwithdebinfo|debug; --num-threads N (also -j).
  --num-workers N (-w); --gpu-ids 0,1,... (-g); --single-container.
  --overwrite-config; --skip-generate-config; --logs-dir PATH.
  --profile; --profile-args STRING; --no-cache; --all-cuda-archs.
  --sccache; --sccache-enable-dist; --sccache-version VERSION.

ENVIRONMENT:
  PRESTO_DEV_PRESTO_SOURCE, PRESTO_DEV_VELOX_SOURCE: source path defaults.
  PRESTO_DEV_BUILD_CONTEXT: parent containing both sources and this checkout
    (default: this checkout's parent). Symlinks escaping the context are rejected.
  DEPS_IMAGE: existing dependency image (default: centos9-\${USER}).
  NATIVE_BUILD_CACHE_SCOPE: optional object-cache namespace. The dependency
    image ID, source paths, build type and architectures always qualify it.
  PRESTO_GPU_UCX_TLS: UCX transports, e.g. tcp,cuda_copy,self to disable IPC.
  PRESTO_GPU_NUMA_BINDING: auto (default), required, or off.
  PRESTO_GPU_EXCHANGE_COMPRESSION: true or false (default). Opt-in uses WXD's
    adaptive codec and requires a compatible Presto/Velox build. Config generation
    must run to apply changes; do not use --skip-generate-config when toggling.
  PRESTO_CTAS_SCRATCH_DIR: main's optional writable CTAS scratch mount.

Worker/coordinator-only restarts keep the other service set running. Use
--clean-first --restart-target all when changing worker layouts or variants.
--restart-target none builds without starting containers. --no-cache bypasses
Docker layers, not the persistent native object cache.
EOF
}

DEV_RESTART_TARGET=all
DEV_CLEAN_FIRST=false
WAIT_FOR_WORKERS=false
WAIT_FOR_WORKERS_TIMEOUT=180
DEV_PRESTO_SOURCE="${PRESTO_DEV_PRESTO_SOURCE:-${REPO_ROOT}/../presto}"
DEV_VELOX_SOURCE="${PRESTO_DEV_VELOX_SOURCE:-${REPO_ROOT}/../velox}"
export PRESTO_DEV_JAVA_CLEAN=false
START_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) print_dev_help; exit 0 ;;
    --presto-source) DEV_PRESTO_SOURCE=${2:?Missing Presto source}; shift 2 ;;
    --velox-source) DEV_VELOX_SOURCE=${2:?Missing Velox source}; shift 2 ;;
    --restart-target) DEV_RESTART_TARGET=${2:?Missing restart target}; shift 2 ;;
    --clean-first) DEV_CLEAN_FIRST=true; shift ;;
    --clean-java) PRESTO_DEV_JAVA_CLEAN=true; shift ;;
    --wait) WAIT_FOR_WORKERS=true; shift ;;
    --wait-timeout) WAIT_FOR_WORKERS_TIMEOUT=${2:?Missing wait timeout}; shift 2 ;;
    *) START_ARGS+=("$1"); shift ;;
  esac
done
[[ "$DEV_RESTART_TARGET" =~ ^(all|worker|coordinator|none)$ ]] || { echo 'Invalid restart target' >&2; exit 1; }
[[ "$WAIT_FOR_WORKERS_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid wait timeout' >&2; exit 1; }
if $DEV_CLEAN_FIRST && [[ "$DEV_RESTART_TARGET" != all ]]; then
  echo '--clean-first requires --restart-target all' >&2; exit 1
fi
if $WAIT_FOR_WORKERS && [[ "$DEV_RESTART_TARGET" == none ]]; then
  echo '--wait requires a restart target other than none' >&2; exit 1
fi
export VARIANT_TYPE=gpu
SCRIPT_NAME="$0"
# Main's parser deliberately permits unset optional environment variables.
source "${SCRIPT_DIR}/start_presto_helper_parse_args.sh" "${START_ARGS[@]}"
source "${SCRIPT_DIR}/common_functions.sh"
export SCCACHE_AUTH_DIR
[[ "$NUM_WORKERS" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid worker count' >&2; exit 1; }
[[ "$NUM_THREADS" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid thread count' >&2; exit 1; }
export GPU_IDS="${GPU_IDS:-$(seq -s, 0 $((NUM_WORKERS - 1)))}"
IFS=, read -ra WORKER_IDS <<< "$GPU_IDS"
if [[ $(printf '%s\n' "${WORKER_IDS[@]}" | sort -u | wc -l) -ne "$NUM_WORKERS" ]]; then
  echo 'GPU IDs must be unique and match --num-workers' >&2; exit 1
fi

DEV_PRESTO_SOURCE=$(realpath -e "$DEV_PRESTO_SOURCE")
DEV_VELOX_SOURCE=$(realpath -e "$DEV_VELOX_SOURCE")
BUILD_CONTEXT=$(realpath -e "${PRESTO_DEV_BUILD_CONTEXT:-${REPO_ROOT}/..}")
[[ -f "$DEV_PRESTO_SOURCE/pom.xml" && -d "$DEV_PRESTO_SOURCE/presto-native-execution" && -d "$DEV_PRESTO_SOURCE/docker" ]] || { echo 'Invalid Presto source tree' >&2; exit 1; }
[[ -f "$DEV_VELOX_SOURCE/CMakeLists.txt" && -d "$DEV_VELOX_SOURCE/velox" ]] || { echo 'Invalid Velox source tree' >&2; exit 1; }
source_path_in_build_context() {
  local relative
  relative=$(realpath --relative-to="$BUILD_CONTEXT" "$1")
  case "$relative" in ../*|..) echo "Source outside build context: $1" >&2; return 1 ;; esac
  printf '%s\n' "$relative"
}
PRESTO_SOURCE_PATH=$(source_path_in_build_context "$DEV_PRESTO_SOURCE")
VELOX_SOURCE_PATH=$(source_path_in_build_context "$DEV_VELOX_SOURCE")
TESTING_SOURCE_PATH=$(source_path_in_build_context "$REPO_ROOT")

# Include dirty and untracked source content, not only the commit identifier.
source_fingerprint() {
  local repo=$1 file
  git -C "$repo" rev-parse HEAD
  git -C "$repo" diff --binary HEAD --
  while IFS= read -r -d '' file; do
    printf '%s\0' "$file"
    if [[ -L "$repo/$file" ]]; then
      readlink "$repo/$file"
    elif [[ -f "$repo/$file" ]]; then
      sha256sum "$repo/$file" | cut -d' ' -f1
    fi
  done < <(git -C "$repo" ls-files --others --exclude-standard -z | sort -z)
}

export PRESTO_IMAGE_TAG="${PRESTO_IMAGE_TAG:-${USER:-latest}}"
export DEPS_IMAGE="${DEPS_IMAGE:-presto/prestissimo-dependency:centos9-${USER:-latest}}"
COORDINATOR_IMAGE="presto-coordinator:${PRESTO_IMAGE_TAG}"
WORKER_IMAGE="presto-native-worker-gpu:${PRESTO_IMAGE_TAG}"
BUILD_COORDINATOR=false
BUILD_WORKER=false
if [[ "$BUILD_TARGET" =~ ^(all|a|coordinator|c)$ ]] || ! docker image inspect "$COORDINATOR_IMAGE" >/dev/null 2>&1; then BUILD_COORDINATOR=true; fi
if [[ "$BUILD_TARGET" =~ ^(all|a|worker|w)$ ]] || ! docker image inspect "$WORKER_IMAGE" >/dev/null 2>&1; then BUILD_WORKER=true; fi

if [[ "$DEV_RESTART_TARGET" != none ]]; then
export LOGS_DIR="${LOGS_DIR:-${SCRIPT_DIR}/presto_logs}"
mkdir -p "$LOGS_DIR"
LOGS_DIR=$(realpath "$LOGS_DIR")
if [[ "$DEV_RESTART_TARGET" != none ]]; then
  mkdir -p "$LOGS_DIR/archive"
  for log in "$LOGS_DIR"/*.log; do
    [[ -f "$log" ]] || continue
    case "$DEV_RESTART_TARGET:$(basename "$log")" in
      all:*|worker:worker_*|coordinator:coordinator_*) mv "$log" "$LOGS_DIR/archive/" ;;
    esac
  done
fi
export SERVER_START_TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
if $DEV_CLEAN_FIRST; then "${SCRIPT_DIR}/stop_presto.sh"; fi
if [[ "$SKIP_GENERATE_CONFIG" != true ]]; then "${SCRIPT_DIR}/generate_presto_config.sh"; fi

source "${REPO_ROOT}/scripts/dev_python_env.sh"
dev_python_activate "${PRESTO_DEV_ORCHESTRATION_VENV:-${REPO_ROOT}/.venv-orchestration}" "${REPO_ROOT}/template_rendering/requirements.txt"
RENDERED_DIR="${REPO_ROOT}/presto/docker/docker-compose/generated"
mkdir -p "$RENDERED_DIR"
COMPOSE_FILE="$RENDERED_DIR/docker-compose.native-gpu.rendered.yml"
python "${REPO_ROOT}/template_rendering/render_docker_compose_template.py" \
  --template-path "${REPO_ROOT}/presto/docker/docker-compose/template/docker-compose.native-gpu.yml.jinja" \
  --output-path "$COMPOSE_FILE" --num-workers "$NUM_WORKERS" --gpu-ids "$GPU_IDS" \
  --single-container "$SINGLE_CONTAINER" --kvikio-threads "$KVIKIO_THREADS" --sccache "$ENABLE_SCCACHE"
WORKER_SERVICES=()
if (( NUM_WORKERS > 1 )) && [[ "$SINGLE_CONTAINER" == false ]]; then
  for id in "${WORKER_IDS[@]}"; do WORKER_SERVICES+=("presto-native-worker-gpu-${id}"); done
else
  WORKER_SERVICES=(presto-native-worker-gpu)
fi
# Keep main's runtime volumes/environment, selecting only the build contexts.
OVERRIDE_FILE="$RENDERED_DIR/docker-compose.gpu-dev.json"
python - "$OVERRIDE_FILE" "$DEV_PRESTO_SOURCE/docker" "$BUILD_CONTEXT" "$REPO_ROOT/presto/docker/native_build.dockerfile" "${WORKER_SERVICES[@]}" <<'PY'
import json
import sys
path, presto, context, dockerfile, *workers = sys.argv[1:]
services = {"presto-coordinator": {"build": {"context": presto}}}
services.update({worker: {"build": {"context": context, "dockerfile": dockerfile}} for worker in workers})
with open(path, "w") as output:
    json.dump({"services": services}, output)
PY
COMPOSE=(docker compose -f "$COMPOSE_FILE" -f "$OVERRIDE_FILE")
"${COMPOSE[@]}" config --quiet
if [[ "$DEV_RESTART_TARGET" == worker ]]; then
  [[ $(docker inspect -f '{{.State.Running}}' presto-coordinator) == true ]] || { echo 'Worker-only restart requires a running coordinator' >&2; exit 1; }
  EXPECTED_NETWORK=$("${COMPOSE[@]}" config --format json | python -c 'import json, sys; print(json.load(sys.stdin)["networks"]["default"]["name"])')
  if ! docker inspect -f '{{json .NetworkSettings.Networks}}' presto-coordinator |
    python -c 'import json, sys; sys.exit(0 if sys.argv[1] in json.load(sys.stdin) else 1)' "$EXPECTED_NETWORK"; then
    echo "Coordinator is not attached to ${EXPECTED_NETWORK}; use --clean-first --restart-target all." >&2
    exit 1
  fi
fi
fi

if $BUILD_COORDINATOR || $BUILD_WORKER; then
  PRESTO_SHA=$(git -C "$DEV_PRESTO_SOURCE" rev-parse HEAD)
  PRESTO_BRANCH=$(git -C "$DEV_PRESTO_SOURCE" rev-parse --abbrev-ref HEAD)
  PRESTO_REPO=$(_strip_url_credentials "$(git -C "$DEV_PRESTO_SOURCE" remote get-url origin 2>/dev/null || true)")
  VELOX_SHA=$(git -C "$DEV_VELOX_SOURCE" rev-parse HEAD)
  VELOX_BRANCH=$(git -C "$DEV_VELOX_SOURCE" rev-parse --abbrev-ref HEAD)
  VELOX_REPO=$(_strip_url_credentials "$(git -C "$DEV_VELOX_SOURCE" remote get-url origin 2>/dev/null || true)")
  PROVENANCE_ARGS=(--build-arg "PRESTO_SHA=$PRESTO_SHA" --build-arg "PRESTO_BRANCH=$PRESTO_BRANCH" --build-arg "PRESTO_REPOSITORY=$PRESTO_REPO")
fi
if $BUILD_COORDINATOR; then
  PRESTO_VERSION=testing PRESTO_DEV_PRESTO_SOURCE="$DEV_PRESTO_SOURCE" "${SCRIPT_DIR}/build_presto_java_package.sh"
  docker build ${SKIP_CACHE_ARG:-} --build-arg PRESTO_VERSION=testing -t "$COORDINATOR_IMAGE" "$DEV_PRESTO_SOURCE/docker"
  docker build --no-cache -f "$REPO_ROOT/presto/docker/provenance_labels.dockerfile" \
    --build-arg "BASE_IMAGE=$COORDINATOR_IMAGE" "${PROVENANCE_ARGS[@]}" \
    -t "$COORDINATOR_IMAGE" "$REPO_ROOT/presto/docker"
fi
if $BUILD_WORKER; then
  DEPS_ID=$(docker image inspect --format '{{.Id}}' "$DEPS_IMAGE")
  [[ "$DEPS_ID" == sha256:* ]] || { echo 'Dependency image is missing or has no content ID' >&2; exit 1; }
  # Pin FROM to an immutable local tag, even if DEPS_IMAGE is retagged mid-build.
  PINNED_DEPS="presto/dev-deps:sha256-${DEPS_ID#sha256:}"
  docker tag "$DEPS_ID" "$PINNED_DEPS"
  if [[ "$ALL_CUDA_ARCHS" == true ]]; then
    CUDA_ARCHITECTURES='75;80;86;90;100;120'
  else
    CUDA_ARCHITECTURES=$(nvidia-smi -i "${WORKER_IDS[0]}" --query-gpu=compute_cap --format=csv,noheader | awk 'NR==1 {gsub(/\./, ""); print}')
    [[ "$CUDA_ARCHITECTURES" =~ ^[0-9]+$ ]] || { echo 'Unable to detect CUDA architecture' >&2; exit 1; }
  fi
  SOURCE_HASH=$({ source_fingerprint "$DEV_PRESTO_SOURCE"; source_fingerprint "$DEV_VELOX_SOURCE"; } | sha256sum | cut -d' ' -f1)
  CACHE_ID=$(printf '%s\n' "${NATIVE_BUILD_CACHE_SCOPE:-gpu-dev}" "$REPO_ROOT" "$DEV_PRESTO_SOURCE" "$DEV_VELOX_SOURCE" "$DEPS_ID" "$BUILD_TYPE" "$CUDA_ARCHITECTURES" | sha256sum | cut -d' ' -f1)
  SCCACHE_ARGS=(--build-arg ENABLE_SCCACHE=OFF)
  if [[ "$ENABLE_SCCACHE" == true ]]; then
    [[ -f "$SCCACHE_AUTH_DIR/github_token" && -f "$SCCACHE_AUTH_DIR/aws_credentials" ]] || { echo 'Missing sccache credentials' >&2; exit 1; }
    SCCACHE_ARGS=(--build-arg ENABLE_SCCACHE=ON --build-arg "SCCACHE_VERSION=$SCCACHE_VERSION"
      --secret "id=github_token,src=$SCCACHE_AUTH_DIR/github_token"
      --secret "id=aws_credentials,src=$SCCACHE_AUTH_DIR/aws_credentials")
    [[ "$SCCACHE_ENABLE_DIST" == true ]] || SCCACHE_ARGS+=(--build-arg SCCACHE_NO_DIST_COMPILE=1)
  fi
  docker build --progress plain ${SKIP_CACHE_ARG:-} -f "$REPO_ROOT/presto/docker/native_build.dockerfile" \
    --build-arg "BASE_IMAGE=$PINNED_DEPS" --build-arg "PRESTO_SOURCE_PATH=$PRESTO_SOURCE_PATH" \
    --build-arg "VELOX_SOURCE_PATH=$VELOX_SOURCE_PATH" --build-arg "TESTING_SOURCE_PATH=$TESTING_SOURCE_PATH" \
    --build-arg "SOURCE_HASH=$SOURCE_HASH" --build-arg "NATIVE_BUILD_CACHE_ID=gpu-dev-$CACHE_ID" \
    --build-arg "BUILD_TYPE=$BUILD_TYPE" --build-arg "NUM_THREADS=$NUM_THREADS" \
    --build-arg "CUDA_ARCHITECTURES=$CUDA_ARCHITECTURES" --build-arg GPU=ON \
    --build-arg 'EXTRA_CMAKE_FLAGS=-DPRESTO_ENABLE_TESTING=OFF -DPRESTO_ENABLE_PARQUET=ON -DPRESTO_ENABLE_S3=ON -DPRESTO_ENABLE_CUDF=ON -DVELOX_ENABLE_UCX_EXCHANGE=ON -DVELOX_BUILD_TESTING=OFF -DPRESTO_STATS_REPORTER_TYPE=PROMETHEUS' \
    "${SCCACHE_ARGS[@]}" "${PROVENANCE_ARGS[@]}" \
    --build-arg "VELOX_SHA=$VELOX_SHA" --build-arg "VELOX_BRANCH=$VELOX_BRANCH" --build-arg "VELOX_REPOSITORY=$VELOX_REPO" \
    -t "$WORKER_IMAGE" "$BUILD_CONTEXT"
fi

case "$DEV_RESTART_TARGET" in
  all)
    rm -f "$LOGS_DIR/worker_provenance.json" "$LOGS_DIR/coordinator_provenance.json"
    "${COMPOSE[@]}" up -d --no-build --force-recreate --remove-orphans ;;
  worker)
    rm -f "$LOGS_DIR/worker_provenance.json"
    "${COMPOSE[@]}" up -d --no-build --no-deps --force-recreate "${WORKER_SERVICES[@]}" ;;
  coordinator)
    rm -f "$LOGS_DIR/coordinator_provenance.json"
    "${COMPOSE[@]}" up -d --no-build --no-deps --force-recreate presto-coordinator ;;
  none) echo 'Build complete; services left unchanged.' ;;
esac
if $WAIT_FOR_WORKERS; then
  python "${SCRIPT_DIR}/gpu_worker_readiness.py" --gpu-ids "$GPU_IDS" \
    --single-container "$SINGLE_CONTAINER" --timeout "$WAIT_FOR_WORKERS_TIMEOUT"
fi
