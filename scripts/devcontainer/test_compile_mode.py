"""CPU-only policy tests; all build commands and sccache daemons are mocked."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("memory", SCRIPTS / "_available_memory.py")
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)
FLOOR = 200_000_000_000


class MemoryTest(unittest.TestCase):
    def fixture(self, kind="cgroup2", available=FLOOR):
        self.files = {
            "/proc/meminfo": f"MemTotal: 999999999999 kB\nMemAvailable: {available // 1024} kB\nSwapFree: 999999999999 kB\n",
            "/proc/self/cgroup": "0::/parent/leaf\n" if kind == "cgroup2" else "5:cpu,memory:/parent/leaf\n",
            "/proc/self/mountinfo": f"1 0 0:1 / /cg rw - {kind} cgroup rw,memory\n",
        }
        self.limit_name, self.usage_name = (("memory.max", "memory.current") if kind == "cgroup2"
                                          else ("memory.limit_in_bytes", "memory.usage_in_bytes"))
        for path in ("/cg", "/cg/parent", "/cg/parent/leaf"):
            self.counters(path, "max" if kind == "cgroup2" else str(2**63 - 4096), 0)

    def counters(self, path, limit, usage):
        self.files[f"{path}/{self.limit_name}"] = str(limit)
        self.files[f"{path}/{self.usage_name}"] = str(usage)

    def probe(self):
        def read(path, *args, **kwargs):
            try:
                value = self.files[str(path)]
                if isinstance(value, Exception):
                    raise value
                return value
            except KeyError:
                raise FileNotFoundError(str(path))
        with patch.object(Path, "read_text", read):
            return memory.available_bytes()

    def test_memavailable_boundaries_and_no_swap_or_total(self):
        for amount in (FLOOR - 1024, FLOOR, FLOOR + 1024):
            with self.subTest(amount=amount):
                self.fixture(available=amount)
                self.assertEqual(self.probe(), amount)
                with patch.object(memory, "available_bytes", return_value=amount):
                    self.assertEqual(memory.main(), int(amount < FLOOR))

    def test_v1_v2_leaf_and_ancestor_headroom(self):
        for kind in ("cgroup", "cgroup2"):
            for path in ("/cg", "/cg/parent", "/cg/parent/leaf"):
                for headroom in (FLOOR - 1, FLOOR, FLOOR + 1):
                    with self.subTest(kind=kind, path=path, headroom=headroom):
                        self.fixture(kind, FLOOR * 2)
                        self.counters(path, headroom + 1234, 1234)
                        self.assertEqual(self.probe(), headroom)

    def test_unlimited_and_exhausted(self):
        for kind in ("cgroup", "cgroup2"):
            self.fixture(kind)
            self.assertEqual(self.probe(), FLOOR)
            self.counters("/cg/parent", 100, 101)
            self.assertEqual(self.probe(), 0)

    def test_subtree_mount_and_escaped_mountpoint(self):
        self.fixture()
        self.files = {p.replace("/cg", "/cg space", 1) if p.startswith("/cg") else p: v
                      for p, v in self.files.items()}
        self.files["/proc/self/mountinfo"] = "1 0 0:1 /parent /cg\\040space/parent rw - cgroup2 cgroup rw\n"
        self.counters("/cg space/parent", FLOOR - 10, 0)
        self.assertEqual(self.probe(), FLOOR - 10)

    def test_prefer_mount_exposing_ancestors(self):
        self.fixture()
        self.files["/proc/self/mountinfo"] += "2 0 0:1 /parent/leaf /other rw - cgroup2 cgroup rw\n"
        self.counters("/cg", 123, 0)
        self.assertEqual(self.probe(), 123)

    def true_v2_root(self):
        del self.files["/cg/memory.max"]
        del self.files["/cg/memory.current"]
        self.files["/cg/cgroup.controllers"] = "cpu memory pids\n"

    def test_hybrid_uses_v1_memory_and_ancestor_headroom(self):
        for unified_first in (False, True):
            for headroom in (FLOOR - 1, FLOOR, FLOOR + 1):
                with self.subTest(unified_first=unified_first, headroom=headroom):
                    self.fixture("cgroup", FLOOR * 2)
                    unified = "0::/parent/leaf\n"
                    v1 = self.files["/proc/self/cgroup"]
                    self.files["/proc/self/cgroup"] = unified + v1 if unified_first else v1 + unified
                    self.files["/proc/self/mountinfo"] += "2 0 0:2 / /unified rw - cgroup2 cgroup rw\n"
                    self.counters("/cg/parent", headroom + 1234, 1234)
                    available = self.probe()
                    self.assertEqual(available, headroom)
                    with patch.object(memory, "available_bytes", return_value=available):
                        self.assertEqual(memory.main(), int(headroom < FLOOR))

    def test_true_v2_root_preserves_host_and_descendant_limits(self):
        for group in ("/", "/parent/leaf"):
            for headroom in (FLOOR - 1, FLOOR, FLOOR + 1):
                with self.subTest(group=group, headroom=headroom):
                    self.fixture(available=FLOOR * 2)
                    self.true_v2_root()
                    self.files["/proc/self/cgroup"] = f"0::{group}\n"
                    self.counters("/cg/parent", headroom + 1234, 1234)
                    expected = FLOOR * 2 if group == "/" else headroom
                    available = self.probe()
                    self.assertEqual(available, expected)
                    with patch.object(memory, "available_bytes", return_value=available):
                        self.assertEqual(memory.main(), int(expected < FLOOR))
        self.files["/proc/meminfo"] = f"MemAvailable: {(FLOOR - 1024) // 1024} kB\n"
        self.assertEqual(self.probe(), FLOOR - 1024)

    def test_missing_root_counters_require_positive_evidence(self):
        for name, value in (
            ("cgroup.type", "domain\n"),  # Also present at a namespace root.
            ("cgroup.type", ""),
            ("cgroup.type", PermissionError("denied")),
            ("memory.current", "0\n"),
            ("memory.current", PermissionError("denied")),
            ("cgroup.controllers", None),
            ("cgroup.controllers", "cpu pids\n"),
            ("cgroup.controllers", PermissionError("denied")),
            ("memory.max", PermissionError("denied")),
        ):
            with self.subTest(name=name, value=value):
                self.fixture()
                self.true_v2_root()
                if value is None:
                    self.files.pop(f"/cg/{name}", None)
                else:
                    self.files[f"/cg/{name}"] = value
                with self.assertRaises((OSError, ValueError)):
                    self.probe()

    def test_missing_subtree_and_non_root_counters_are_not_unlimited(self):
        for subtree_mount in (False, True):
            with self.subTest(subtree_mount=subtree_mount):
                self.fixture()
                self.true_v2_root()
                if subtree_mount:
                    self.files["/proc/self/mountinfo"] = "1 0 0:1 /parent /cg rw - cgroup2 cgroup rw\n"
                    self.files["/proc/self/cgroup"] = "0::/parent\n"
                else:
                    del self.files["/cg/parent/memory.max"]
                    del self.files["/cg/parent/memory.current"]
                    self.files["/cg/parent/cgroup.controllers"] = "memory\n"
                with self.assertRaises(FileNotFoundError):
                    self.probe()

    def test_missing_malformed_or_unreadable_fails_closed(self):
        mutations = {
            "/proc/meminfo": [None, "MemTotal: 999999999999 kB\n", "MemAvailable: -1 kB\n", "MemAvailable: 200 GB\n"],
            "/proc/self/cgroup": [None, "", "0::/../escape", "invalid"],
            "/proc/self/mountinfo": [None, "", "malformed"],
            "/cg/parent/memory.max": [None, "", "-1", "MAX", "1.5"],
            "/cg/parent/memory.current": [None, "", "-1", "max"],
        }
        for path, values in mutations.items():
            for value in values:
                with self.subTest(path=path, value=value):
                    self.fixture()
                    if value is None:
                        del self.files[path]
                    else:
                        self.files[path] = value
                    with self.assertRaises((OSError, ValueError, IndexError)):
                        self.probe()
        with patch.object(memory, "available_bytes", side_effect=PermissionError("denied")):
            self.assertEqual(memory.main(), 1)


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for path in SCRIPTS.iterdir():
            if path.is_file() and not path.name.startswith("test"):
                shutil.copy(path, self.bin / path.name)
        self.config = self.root / "velox-testing/.devcontainer/config.env"
        self.config.parent.mkdir(parents=True)
        self.base_config = (SCRIPTS.parents[1] / ".devcontainer/config.env").read_text()
        self.config.write_text(self.base_config)
        (self.root / "presto/presto-native-execution").mkdir(parents=True)
        (self.root / "velox").mkdir()
        self.log = self.root / "calls"
        self.env = {**os.environ, "HOME": str(self.root), "BASH_ENV": "/dev/null",
                    "PATH": f"{self.bin}:/usr/bin:/bin", "CALLS": str(self.log),
                    "SCCACHE_NO_DIST_COMPILE": "1", "SCCACHE_BASEDIRS": str(self.root / "velox"),
                    "FB_DEPS_PREFIX": str(self.root / "fb-deps")}
        self.stub("python3", 'echo memory >>"$CALLS"; exit "${MEMORY_RC:-0}"')
        self.stub("git", 'printf "worktree %s/velox\\n" "$HOME"')
        self.stub("sccache", '''echo "sccache $*" >>"$CALLS"
case "$1" in
 --show-adv-stats) printf '%s\\n' "$STATS" ;;
 --dist-status) printf '%s\\n' "$STATUS" ;;
 --dist-auth) exit "${AUTH_RC:-0}" ;;
esac''')
        for name in ("cmake", "make", "ninja", "rsync", "curl", "build-fb-deps"):
            self.stub(name, 'echo "work $0 $* mode=$SCCACHE_COMPILE_MODE nodist=${SCCACHE_NO_DIST_COMPILE-unset} launcher=$CMAKE_CXX_COMPILER_LAUNCHER fallback=$SCCACHE_DIST_FALLBACK_TO_LOCAL_COMPILE" >>"$CALLS"')
        self.stats = {"basedirs": [str(self.root / "velox")], "stats": {
            "requests_not_cacheable": 0, "requests_unsupported_compiler": 0,
            "non_cacheable_compilations": 0, "dist_errors": 0, "dist_compiles": {},
            "active_compilations": 0, "queued_compilations": 0, "pending_compilations": 0}}
        self.env["STATUS"] = json.dumps({"SchedulerStatus": [None, {"servers": ["worker"]}]})

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def mode(self, value):
        self.config.write_text(self.base_config.replace("SCCACHE_COMPILE_MODE=remote", value))

    def shell(self, code):
        env = {**self.env, "STATS": json.dumps(self.stats)}
        return subprocess.run(["bash", "--noprofile", "--norc", "-c", code], env=env,
                              text=True, capture_output=True, timeout=5)

    def helper(self, code):
        return self.shell(f'set -e; source "{self.bin}/_devcontainer_common_utils.sh"; '
                          'mkdir() { :; }; select_sccache_family rapids; ' + code)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_default_explicit_remote_and_invalid(self):
        for mode in ("", "SCCACHE_COMPILE_MODE=remote"):
            self.mode(mode)
            result = self.helper('require_sccache; echo "$SCCACHE_COMPILE_MODE:${SCCACHE_NO_DIST_COMPILE-unset}:$SCCACHE_DIST_FALLBACK_TO_LOCAL_COMPILE"')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("remote:unset:false", result.stdout)
            self.assertNotIn("memory", self.calls())
            self.assertIn("--dist-auth", self.calls())
            self.assertIn("--dist-status", self.calls())
        for mode in ("invalid", "", "LOCAL"):
            self.mode(f"SCCACHE_COMPILE_MODE='{mode}'")
            self.assertNotEqual(self.helper("echo unexpected").returncode, 0)

    def test_local_cached_launchers_and_no_auth(self):
        self.mode("SCCACHE_COMPILE_MODE=local")
        result = self.helper('require_sccache; bash -c \'echo "$SCCACHE_COMPILE_MODE:$SCCACHE_NO_DIST_COMPILE:$CMAKE_C_COMPILER_LAUNCHER:$CMAKE_CXX_COMPILER_LAUNCHER:$CMAKE_CUDA_COMPILER_LAUNCHER:$SCCACHE_DIST_FALLBACK_TO_LOCAL_COMPILE"\'')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("local:1:sccache:sccache:sccache:false", result.stdout)
        self.assertTrue(self.calls().startswith("memory\n"))
        self.assertNotIn("--dist-", self.calls())

    def test_remote_auth_scheduler_and_active_basedirs_still_fail(self):
        for key, value in (("AUTH_RC", "1"), ("STATUS", "{}")):
            self.env[key] = value
            self.assertNotEqual(self.helper("require_sccache").returncode, 0)
            del self.env[key]
        self.env["STATUS"] = "{}"
        self.stats["basedirs"] = ["/different"]
        self.stats["stats"]["active_compilations"] = 1
        for mode in ("remote", "local"):
            self.mode(f"SCCACHE_COMPILE_MODE={mode}")
            self.assertNotEqual(self.helper("require_sccache").returncode, 0)
            self.assertNotIn("--stop-server", self.calls())

    def test_stats_policy_both_modes(self):
        for mode in ("remote", "local"):
            self.mode(f"SCCACHE_COMPILE_MODE={mode}")
            self.assertEqual(self.helper("validate_sccache_stats").returncode, 0)
            for key in ("requests_not_cacheable", "requests_unsupported_compiler", "non_cacheable_compilations", "dist_errors"):
                self.stats["stats"][key] = 1
                self.assertNotEqual(self.helper("validate_sccache_stats").returncode, 0)
                self.stats["stats"][key] = 0
            self.stats["stats"]["dist_compiles"] = {"worker": 1}
            self.assertEqual(self.helper("validate_sccache_stats").returncode == 0, mode == "remote")
            self.stats["stats"]["dist_compiles"] = {}

    def test_all_wrappers_stop_before_work_on_memory_failure(self):
        self.mode("SCCACHE_COMPILE_MODE=local")
        self.env["MEMORY_RC"] = "1"
        # Restore the actual dependency wrapper for this gate test.
        shutil.copy(SCRIPTS / "build-fb-deps", self.bin / "build-fb-deps")
        for name in ("build-velox", "configure-velox", "build-presto", "build-fb-deps",
                     "build-libcudf-cpp", "configure-libcudf-cpp", "build-all", "build-all-cpp", "configure-all"):
            with self.subTest(wrapper=name):
                self.log.unlink(missing_ok=True)
                result = self.shell(f'bash "{self.bin / name}"')
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(self.calls(), "memory\n", result.stderr)

    def test_configure_propagates_mode_and_keeps_launchers(self):
        self.mode("SCCACHE_COMPILE_MODE=local")
        result = self.shell(f'bash "{self.bin}/configure-libcudf-cpp"')
        self.assertEqual(result.returncode, 0, result.stderr)
        for launcher in ("C", "CXX", "CUDA"):
            self.assertIn(f"-DCMAKE_{launcher}_COMPILER_LAUNCHER=sccache", self.calls())
        self.assertIn("mode=local nodist=1 launcher=sccache fallback=false", self.calls())
        self.assertNotIn("--dist-", self.calls())

    def test_incremental_build_propagates_local_mode(self):
        self.mode("SCCACHE_COMPILE_MODE=local")
        source = self.root / "cudf/cpp"
        build = source / "build/latest"
        build.mkdir(parents=True)
        cache = {"CMAKE_HOME_DIRECTORY": str(source), "BUILD_TESTS": "ON",
                 "BUILD_BENCHMARKS": "ON", "CMAKE_CUDA_ARCHITECTURES": "native"}
        cache.update({f"CMAKE_{language}_COMPILER_LAUNCHER": "sccache"
                      for language in ("C", "CXX", "CUDA")})
        (build / "CMakeCache.txt").write_text("".join(f"{key}:STRING={value}\n" for key, value in cache.items()))
        result = self.shell(f'bash "{self.bin}/build-libcudf-cpp"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--build", self.calls())
        self.assertIn("mode=local nodist=1 launcher=sccache fallback=false", self.calls())
        self.assertNotIn("--dist-", self.calls())

    def test_remote_policy_failure_retains_guarded_retry(self):
        self.stats["stats"]["dist_errors"] = 1
        result = self.shell(f'bash "{self.bin}/configure-libcudf-cpp"')
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.calls().count("work "), 2)
        self.assertIn("--stop-server", self.calls())
        self.assertIn("--start-server", self.calls())

    def test_configure_allows_non_cacheable_probes(self):
        for mode in ("remote", "local"):
            self.mode(f"SCCACHE_COMPILE_MODE={mode}")
            self.stats["stats"]["requests_not_cacheable"] = 1
            self.stats["stats"]["non_cacheable_compilations"] = 1
            result = self.shell(f'bash "{self.bin}/configure-libcudf-cpp"')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_policy_failure_does_not_restart_or_retry(self):
        self.mode("SCCACHE_COMPILE_MODE=local")
        self.stats["stats"]["dist_errors"] = 1
        result = self.shell(f'bash "{self.bin}/configure-libcudf-cpp"')
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.calls().count("work "), 1)
        self.assertNotIn("--stop-server", self.calls())


if __name__ == "__main__":
    unittest.main()
