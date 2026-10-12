# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("benchmark_exit,validation_exit,expected", [(0, 0, 0), (1, 0, 1), (2, 1, 2), (0, 1, 1)])
def test_validation_runs_after_benchmark_and_preserves_failure(tmp_path, benchmark_exit, validation_exit, expected):
    scripts = tmp_path / "presto/scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).with_name("run_benchmark.sh")
    shutil.copy(source, scripts / source.name)
    (scripts / "presto_connection_defaults.sh").write_text("set_presto_coordinator_defaults() { :; }\n")
    (scripts / "common_functions.sh").write_text("wait_for_worker_node_registration() { :; }\n")
    shared = tmp_path / "scripts"
    shared.mkdir()
    (shared / "py_env_functions.sh").write_text(
        "init_python_virtual_env() { :; }\ndelete_python_virtual_env() { :; }\n"
        "pip() { :; }\npytest() { printf '%s\\n' \"$*\" > \"$PYTEST_ARGS_LOG\"; return \"$BENCHMARK_EXIT\"; }\n"
    )
    validator = shared / "run_py_script.sh"
    validator.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$VALIDATION_ARGS_LOG"\nexit "$MOCK_VALIDATION_EXIT"\n')
    validator.chmod(0o755)
    references = tmp_path / "references"
    references.mkdir()
    env = dict(os.environ, BENCHMARK_EXIT=str(benchmark_exit), MOCK_VALIDATION_EXIT=str(validation_exit),
               PYTEST_ARGS_LOG=str(tmp_path / "pytest.args"), VALIDATION_ARGS_LOG=str(tmp_path / "validation.args"))
    result = subprocess.run(
        ["bash", str(scripts / source.name), "-b", "tpch", "-s", "test", "--cold", "--metrics",
         "-q", "1,2", "--reference-results-dir", str(references)], env=env, capture_output=True, text=True,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert "--cold" in (tmp_path / "pytest.args").read_text()
    assert "--metrics" in (tmp_path / "pytest.args").read_text()
    validation_args = (tmp_path / "validation.args").read_text()
    assert str(references) in validation_args
    assert "--queries\n1,2\n" in validation_args
