# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION.
# SPDX-License-Identifier: Apache-2.0

"""Exercise launcher orchestration without Docker builds or service changes."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "testing"
        scripts = self.repo / "presto/scripts"
        scripts.mkdir(parents=True)
        for filename in ("start_native_gpu_presto_dev.sh", "start_presto_helper_parse_args.sh", "common_functions.sh"):
            shutil.copy(ROOT / "presto/scripts" / filename, scripts / filename)
        self.write("testing/scripts/dev_python_env.sh", 'dev_python_activate() { echo venv >> "$CALLS"; }\n')
        self.write(
            "testing/template_rendering/render_docker_compose_template.py",
            'import os\nwith open(os.environ["CALLS"], "a") as output: output.write("render\\n")\n',
        )
        self.write(
            "testing/presto/scripts/build_presto_java_package.sh",
            '#!/bin/bash\nprintf \'java:%s:%s\\n\' "$PRESTO_DEV_PRESTO_SOURCE" "$PRESTO_DEV_JAVA_CLEAN" >> "$CALLS"\n',
        )
        self.write("testing/presto/scripts/generate_presto_config.sh", '#!/bin/bash\necho config >> "$CALLS"\n')
        self.write("testing/presto/scripts/stop_presto.sh", '#!/bin/bash\necho stop >> "$CALLS"\n')
        for name in ("selected-presto", "selected-velox"):
            path = self.base / name
            path.mkdir()
            subprocess.run(["git", "init", "-q", str(path)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(path),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.org",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "test",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(path), "remote", "add", "origin", "https://user:secret@example.org/repo"], check=True
            )
        self.write("selected-presto/pom.xml", "test")
        (self.base / "selected-presto/presto-native-execution").mkdir()
        (self.base / "selected-presto/docker").mkdir()
        self.write("selected-velox/CMakeLists.txt", "test")
        (self.base / "selected-velox/velox").mkdir()
        self.write("bin/nvidia-smi", "#!/bin/bash\necho 9.0\n")
        self.write(
            "bin/docker",
            """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as output:
    output.write(json.dumps(args) + "\\n")
if args[:2] == ["image", "inspect"] and "--format" in args:
    print(os.environ.get("DEPS_ID", "sha256:aaaa"))
elif args[:1] == ["inspect"]:
    if "{{json .NetworkSettings.Networks}}" in args:
        print(json.dumps({os.environ.get("COORDINATOR_NETWORK", "testing_default"): {}}))
    else:
        print("true")
elif args[-3:] == ["config", "--format", "json"]:
    print(json.dumps({"networks": {"default": {"name": "testing_default"}}}))
""",
        )
        self.env = {
            **os.environ,
            "PATH": f"{self.base}/bin:{os.environ['PATH']}",
            "CALLS": str(self.base / "calls"),
            "PRESTO_DEV_PRESTO_SOURCE": str(self.base / "selected-presto"),
            "PRESTO_DEV_VELOX_SOURCE": str(self.base / "selected-velox"),
            "PRESTO_GPU_UCX_TLS": "tcp,cuda_copy,self",
        }
        self.launcher = scripts / "start_native_gpu_presto_dev.sh"

    def write(self, path, text):
        path = self.base / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)

    def run_launcher(self, *args):
        (self.base / "calls").write_text("")
        result = subprocess.run(
            ["bash", str(self.launcher), "-w", "2", "-g", "2,5", *args], env=self.env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.base / "calls").read_text().splitlines()
        return [json.loads(line) for line in calls if line.startswith("[")], calls

    def test_targeted_restart_does_not_stop_coordinator(self):
        calls, raw = self.run_launcher("--restart-target", "worker")
        self.assertNotIn("stop", raw)
        up = next(call for call in calls if "up" in call)
        self.assertIn("--no-deps", up)
        self.assertEqual(up[-2:], ["presto-native-worker-gpu-2", "presto-native-worker-gpu-5"])
        self.assertFalse(any(call[0] == "build" for call in calls))

    def test_reject_worker_restart_on_foreign_network(self):
        self.env["COORDINATOR_NETWORK"] = "presto_dev"
        result = subprocess.run(
            ["bash", str(self.launcher), "--restart-target", "worker", "--build", "worker"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--clean-first --restart-target all", result.stderr)
        calls = (self.base / "calls").read_text().splitlines()
        docker_calls = [json.loads(line) for line in calls if line.startswith("[")]
        self.assertFalse(any("up" in call or call[0] == "build" for call in docker_calls))

    def test_build_only_preserves_runtime_artifacts(self):
        files = (
            "presto/docker/config/generated/gpu/etc_worker/config_native.properties",
            "presto/docker/docker-compose/generated/docker-compose.native-gpu.rendered.yml",
            "presto/docker/docker-compose/generated/docker-compose.gpu-dev.json",
            "presto/scripts/presto_logs/worker_0.log",
            "presto/scripts/presto_logs/worker_provenance.json",
        )
        for filename in files:
            self.write(f"testing/{filename}", f"existing {filename}\n")
        before = {path: path.read_bytes() for path in self.repo.rglob("*") if path.is_file()}
        calls, raw = self.run_launcher("--restart-target", "none", "--build", "worker", "--overwrite-config")
        after = {path: path.read_bytes() for path in self.repo.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.repo / "presto/scripts/presto_logs/archive").exists())
        self.assertFalse(any(call[0] == "compose" for call in calls))
        for operation in ("config", "render", "venv", "stop"):
            self.assertNotIn(operation, raw)

    def test_build_selected_sources_and_cache_identity(self):
        def build_args():
            calls, _ = self.run_launcher("--build", "worker", "--restart-target", "none", "--build-type", "release")
            self.assertFalse(any("up" in call for call in calls))
            build = next(call for call in calls if call[0] == "build")
            self.assertIn("PRESTO_SOURCE_PATH=selected-presto", build)
            self.assertIn("VELOX_SOURCE_PATH=selected-velox", build)
            self.assertIn("TESTING_SOURCE_PATH=testing", build)
            self.assertIn("PRESTO_REPOSITORY=https://example.org/repo", build)
            self.assertNotIn("secret", " ".join(build))
            return {arg.split("=", 1)[0]: arg.split("=", 1)[1] for arg in build if "=" in arg}

        original = build_args()
        self.write("selected-velox/untracked.cpp", "changed source")
        changed = build_args()
        self.assertNotEqual(original["SOURCE_HASH"], changed["SOURCE_HASH"])
        self.assertEqual(original["NATIVE_BUILD_CACHE_ID"], changed["NATIVE_BUILD_CACHE_ID"])
        self.env["DEPS_ID"] = "sha256:bbbb"
        dependencies = build_args()
        self.assertNotEqual(changed["NATIVE_BUILD_CACHE_ID"], dependencies["NATIVE_BUILD_CACHE_ID"])
        self.assertEqual(dependencies["BASE_IMAGE"], "presto/dev-deps:sha256-bbbb")

    def test_all_build_clean_and_main_java_builder(self):
        calls, raw = self.run_launcher("--build", "all", "--clean-first", "--clean-java", "--overwrite-config")
        self.assertIn("stop", raw)
        self.assertIn(f"java:{self.base}/selected-presto:true", raw)
        self.assertEqual(len([call for call in calls if call[0] == "build"]), 3)
        self.assertTrue(any("--remove-orphans" in call for call in calls))

    def test_reject_unsafe_options_before_docker(self):
        for args in (
            ("--clean-first", "--restart-target", "worker"),
            ("--wait", "--restart-target", "none"),
            ("-w", "2", "-g", "1,1"),
            ("--presto-source", "/tmp"),
        ):
            with self.subTest(args=args):
                result = subprocess.run(
                    ["bash", str(self.launcher), *args], env=self.env, capture_output=True, text=True
                )
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
