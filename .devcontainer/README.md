# Velox + Presto Devcontainer

A GPU-ready development environment for building [Velox](https://github.com/facebookincubator/velox) with cuDF acceleration and [Presto Native Execution](https://github.com/prestodb/presto/tree/master/presto-native-execution). Based on the RAPIDS devcontainer image with scripts for building cuDF, RMM, UCXX, and KvikIO from source.

> **Mounted-repository subset:** the CUDA devcontainer configurations intentionally mount only the sibling `cudf`, `rmm`, and `velox` repositories. `kvikio`, `presto`, `rapidsmpf`, and `ucxx` are not mounted; add those mounts back only when using workflows that require them.

## Quick Start

Standalone Velox-cuDF builds use the distributed build cluster via `sccache-dist` and default to 32 jobs. They build Velox-cuDF libraries and tests plus their library dependencies, but not core Velox test, fuzzer, or benchmark executables.

```bash
# 1. Clone sibling repos under the same parent directory
mkdir ~/code && cd ~/code
git clone https://github.com/rapidsai/velox-testing.git
git clone https://github.com/rapidsai/cudf.git
git clone https://github.com/rapidsai/kvikio.git
git clone https://github.com/prestodb/presto.git
git clone https://github.com/rapidsai/rapidsmpf.git
git clone https://github.com/rapidsai/rmm.git
git clone https://github.com/rapidsai/ucxx.git
git clone https://github.com/facebookincubator/velox.git

# 2. Open in VS Code (or any devcontainer-compatible editor)
code velox-testing

# 3. Reopen in container (pick container based on CUDA version)

# 4. Build
build-all -j4       # Build RAPIDS libraries, standalone Velox, then Presto
build-velox         # Build Velox-cuDF tests with bundled RAPIDS dependencies
build-presto -j4    # Build Presto; syncs ~/velox and builds it inside the Presto tree

# 5. Test
test-velox      # Run Velox test suite
test-presto     # Run Presto test suite
```

## Directory Layout

The devcontainer expects this layout on the host:

```
~/code/
├── cudf/              # rapidsai/cudf
├── kvikio/            # rapidsai/kvikio
├── presto/            # prestodb/presto
├── rapidsmpf/         # rapidsai/rapidsmpf
├── rmm/               # rapidsai/rmm
├── ucxx/              # rapidsai/ucxx
├── velox/             # facebookincubator/velox
└── velox-testing/     # this repo (devcontainer workspace root)
```

All repos are bind-mounted into the container under `/home/coder/`.

## Build Architecture

`build-velox` builds the Velox-cuDF targets from the current Velox worktree,
or from `~/velox` when invoked elsewhere:

```
source: <current Velox worktree>
build:  <current Velox worktree>/build/<release|debug>
mode:   VELOX_MONO_LIBRARY=OFF, VELOX_BUILD_SHARED=OFF
```

For the primary checkout, `/opt/velox-build` is a bind mount of `~/velox/build`,
so both paths refer to the same build tree.

`build-presto` builds Presto and Velox together:

```
source: ~/presto/presto-native-execution
build:  /opt/presto-build
deps:   /opt/fb-deps for folly, fbthrift, proxygen, and related libraries
velox:  rsync ~/velox/ into ~/presto/presto-native-execution/velox/
```

The Presto build consumes **cudf**, **rmm**, **ucxx**, and **kvikio** built from source in the RAPIDS devcontainer.

**Standalone Velox** bundles its pinned RAPIDS and other dependencies by default. Pass `--local-rapids` only when explicitly testing sibling cuDF/RMM build outputs.

**Presto** requires Facebook's OSS stack (folly, fbthrift, proxygen, etc.) for its thrift RPC layer. `build-presto` builds these automatically on first run and caches them at `/opt/fb-deps`. Subsequent runs skip this step unless `--rebuild-deps` is passed.

### When Velox Builds Twice

`build-presto` does not require `build-velox` to run first. It copies `~/velox` into `~/presto/presto-native-execution/velox` with `rsync`, then Presto's CMake build compiles Velox through `add_subdirectory(velox)`.

Velox builds twice only when you run `build-all` or `build-all-cpp`: once as the standalone `~/velox/build` tree, then again inside the Presto build tree under `/opt/presto-build`.

## Commands

| Command | Description |
|---------|-------------|
| `configure-libcudf-cpp` | Configure libcudf tests and benchmarks for remote native-architecture compilation |
| `build-libcudf-cpp` | Incrementally build libcudf, tests, and benchmarks without installing |
| `build-velox` | Build Velox-cuDF libraries and tests (fully self-contained) |
| `build-presto` | Build Presto (auto-builds FB deps on first run) |
| `configure-all` | Configure all build trees |
| `configure-velox` | CMake configure only (for IDE integration) |
| `test-velox` | Run Velox tests via ctest |
| `test-presto` | Run Presto tests via ctest |
| `clean-all` | Delete all build artifacts |
| `clean-velox` | Delete Velox build artifacts |
| `clean-presto` | Delete Presto build artifacts |
| `uninstall-all` | Remove installed build outputs |
| `pre-commit` | Wrapper around `pre-commit` that strips devcontainer build-tuning vars (see below) |
| `rapids-make-pip-env` | Recreate Python environments |
| `devcontainer-utils-sccache-dist-status` | Check distributed build cluster status |

All commands accept `--help`. Common options:

```bash
configure-libcudf-cpp          # configure without building or installing
build-libcudf-cpp              # 32-job native-architecture libcudf build
build-velox                    # 32-job Velox-cuDF library and test build
build-velox --debug            # debug Velox-cuDF build
build-velox --local-rapids     # explicitly use sibling RAPIDS builds
build-velox --benchmarks       # also build Velox-cuDF benchmarks
build-presto --release         # release build (default)
build-presto --rebuild-deps    # force rebuild of FB deps
```

### Running pre-commit hooks on Velox

Always invoke the wrapper, not the venv's `pre-commit` directly:

```bash
/home/coder/velox-testing/scripts/devcontainer/pre-commit run
```

The wrapper unsets two devcontainer environment variables that break hook
environments (CI does not set them, so hooks assume a vanilla shell):

- `RUSTC_WRAPPER` (sccache): sccache sandboxes compiles with bubblewrap, which
  hides the temporary rustup toolchain pre-commit bootstraps for Rust hooks
  (`action-validator`), failing with `bwrap: execvp .../rustc: No such file or directory`.
- `PYTHONSAFEPATH=1`: Python stops adding the script's directory to `sys.path`,
  breaking Velox's `run-clang-tidy.py` (`ModuleNotFoundError: No module named 'util'`).

The wrapper defaults `BUILD_PATH` to `/opt/velox-build/release` for the primary
checkout so the `clang-tidy` hook finds `compile_commands.json` (it is silently
skipped otherwise). Set `BUILD_PATH=<worktree>/build/release` when validating a
linked worktree.

## CUDA Variants

Devcontainer configurations are provided for CUDA 13 and CUDA 12. VS Code will prompt you to choose when opening the workspace.

The `CUDAARCHS` environment variable defaults to `native`, so only the GPU architecture visible in the container is compiled.

## Distributed compilation

Builds require sccache-dist and disable fallback to local compilation. Each build family uses a dedicated local sccache client daemon while sharing the same remote scheduler and worker fleet:

| Family | Port | State directory |
|--------|-----:|-----------------|
| RAPIDS/cuDF | 4227 | `/tmp/sccache/rapids` |
| Velox | 4228 | `/tmp/sccache/velox` |
| Presto | 4229 | `/tmp/sccache/presto` |
| Facebook dependencies | 4230 | `/tmp/sccache/fb-deps` |

This isolation allows a failed build to restart its own client daemon and retry without interrupting another build family. Do not run concurrent builds within one family because they share statistics used to verify that compilation remained remote.

The Velox client normalizes all registered Git worktree roots using
`SCCACHE_BASEDIRS`. Because every worktree uses the same relative `build/`
layout, unchanged source and bundled-dependency compilations can share remote
cache entries across worktrees. The client restarts automatically before a
build when the registered worktree set changes; it refuses to do so while a
Velox compilation is active. Setup verifies and prints the server's actual base
directories before proceeding.

For direct `cmake --build` commands, initialize the same environment in the
shell that launches compilation, including inside a detached `bash -c`:

```bash
source /usr/local/bin/_devcontainer_common_utils.sh
select_sccache_family velox
require_remote_sccache
sccache --zero-stats
cmake --build "$PWD/build/release" \
  --target velox/experimental/cudf/tests/all --parallel 32
```

Running `configure-velox` first does not initialize its parent shell: its
exported settings only reach its own children. A bare build from that parent
can use a different daemon without path normalization. Remote compilation
alone does not establish cross-worktree cache reuse; check the selected
server's base directories and cache-hit statistics. Source content and
compiler flags must also match for reuse.

The devcontainer overrides `/usr/bin/build-cudf-cpp` and `/usr/bin/configure-cudf-cpp` with policy wrappers. Calls from RAPIDS `build-all`, `build-all-cpp`, and `configure-all` resolve those wrappers through a policy directory prepended to `PATH`. Consequently every libcudf entry point enforces native CUDA architecture, tests and benchmarks, the bundled nvCOMP selected by the libcudf build, remote-only sccache, at most 32 jobs, incremental builds when the cache is compatible, and no CMake install target.

## Build Outputs

| Build | Location | Contents |
|-------|----------|----------|
| FB deps | `/opt/fb-deps/` | folly, fbthrift, proxygen (auto-built by `build-presto`) |
| Velox | `<worktree>/build/release/` (`/opt/velox-build/release/` for `~/velox`) | Velox-cuDF libraries and tests plus required libraries |
| Presto | `/opt/presto-build/` | `presto_server` binary, tests |

The primary Velox build is mounted at both `~/velox/build` and
`/opt/velox-build`. Linked worktree builds live under each worktree's ignored
`build/` directory. These host-mounted paths persist across container sessions.

## Dependency Resolution

Velox has many dependencies. Each can be `SYSTEM` (pre-installed) or `BUNDLED` (built from source via FetchContent). The build scripts set these automatically:

| Dependency | Velox Build | Presto Build | Source |
|------------|------------|--------------|--------|
| folly | SYSTEM | SYSTEM | `/opt/fb-deps` |
| xsimd | BUNDLED | SYSTEM | Built from source / `/opt/fb-deps` |
| cudf | BUNDLED | SYSTEM | Velox-pinned source / RAPIDS build |
| rmm | BUNDLED | SYSTEM | Velox-pinned source / RAPIDS build |
| ucxx | BUNDLED | SYSTEM | Velox-pinned source / RAPIDS build |
| kvikio | BUNDLED | SYSTEM | Velox-pinned source / RAPIDS build |
| Arrow | BUNDLED | BUNDLED | Built from source |
| DuckDB | BUNDLED | BUNDLED | Built from source |
| GTest | BUNDLED | BUNDLED | Built from source |
| simdjson | BUNDLED | BUNDLED | Built from source |
| geos | BUNDLED | BUNDLED | Built from source |

## Workarounds

The build scripts include several workarounds for toolchain issues:

- **GCC 14** instead of GCC 13: avoids false-positive `-Wstringop-overflow` in system `fmt` v9.
- **`-DCMAKE_CXX_SCAN_FOR_MODULES=OFF`**: CMake 4.x + GCC 14 + Ninja triggers `-fmodules-ts` which causes GCC 14 ICE (segfault).
- **`-no-pie` linker flag** (Presto only): fbthrift static archives have construction vtables with hidden visibility from virtual inheritance in `apache::thrift` exception classes. The linker cannot resolve `R_X86_64_PC32` relocations against hidden symbols in PIE executables.
- **`-Wno-error=nonnull`** (Presto only): Presto's `SystemConnector.cpp` triggers a false-positive `this` null check warning.

## Scripts

All devcontainer scripts live in `scripts/devcontainer/` and are installed to `/usr/local/bin/` in the container image. They share common functions via `_devcontainer_common_utils.sh`.
