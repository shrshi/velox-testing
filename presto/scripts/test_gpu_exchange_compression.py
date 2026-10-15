# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION.
# SPDX-License-Identifier: Apache-2.0

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def config_tree(tmp_path):
    presto = Path(__file__).resolve().parents[1]
    shutil.copytree(presto / "docker/config/template", tmp_path / "presto/docker/config/template")
    scripts = tmp_path / "presto/scripts"
    scripts.mkdir(parents=True)
    shutil.copy(presto / "scripts/generate_presto_config.sh", scripts)
    pbench = tmp_path / "presto/pbench/pbench"
    pbench.parent.mkdir()
    pbench.write_text('#!/bin/bash\ncp -r template/etc_* "${@: -1}/"\n')
    pbench.chmod(0o755)
    return tmp_path / "presto"


def generate(tree, compression=None, overwrite=False, variant="gpu"):
    env = dict(
        os.environ, VARIANT_TYPE=variant, NUM_WORKERS="2", GPU_IDS="0,2", OVERWRITE_CONFIG=str(overwrite).lower()
    )
    env.pop("PRESTO_GPU_EXCHANGE_COMPRESSION", None)
    if compression is not None:
        env["PRESTO_GPU_EXCHANGE_COMPRESSION"] = compression
    return subprocess.run(
        ["bash", str(tree / "scripts/generate_presto_config.sh")], env=env, capture_output=True, text=True
    )


def properties(path):
    pairs = [
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    ]
    return dict(pairs)


@pytest.mark.parametrize("enabled", [None, "false", "true"])
def test_compression_profile(config_tree, enabled):
    result = generate(config_tree, enabled)
    assert result.returncode == 0, result.stdout + result.stderr
    template = properties(config_tree / "docker/config/template/overrides/gpu/etc_worker/config_native.properties")
    for worker in ("etc_worker", "etc_worker_0", "etc_worker_2"):
        config = properties(config_tree / f"docker/config/generated/gpu/{worker}/config_native.properties")
        assert config["cudf.exchange_compression"] == (
            "column-adaptive-freq-pfor-min128" if enabled == "true" else "none"
        )
        assert config["cudf.exchange_compression_pipeline"] == str(enabled == "true").lower()
        if enabled == "true":
            assert config["cudf.exchange_compression_pipeline_threads"] == "1"
            assert config["cudf.exchange_compression_min_bytes"] == "16777216"
            assert config["cudf.exchange_compression_safety_margin"] == "1.50"
        else:
            assert "cudf.exchange_compression_min_bytes" not in config
        for key, value in template.items():
            if key not in ("cudf.exchange", "cudf.exchange.server.port") and not key.startswith(
                "cudf.exchange_compression"
            ):
                assert config[key] == value


def test_reused_configs_toggle_without_duplicates(config_tree):
    for enabled in ("true", "false", "true", None):
        result = generate(config_tree, enabled)
        assert result.returncode == 0, result.stdout + result.stderr
        for worker in ("etc_worker", "etc_worker_0", "etc_worker_2"):
            path = config_tree / f"docker/config/generated/gpu/{worker}/config_native.properties"
            lines = path.read_text().splitlines()
            assert sum(line.startswith("cudf.exchange_compression=") for line in lines) == 1
            assert properties(path)["cudf.exchange_compression"] == (
                "column-adaptive-freq-pfor-min128" if enabled == "true" else "none"
            )
            if enabled != "true":
                assert not any(line.startswith("cudf.exchange_compression_min_bytes=") for line in lines)


def test_invalid_switch_fails_before_generation(config_tree):
    result = generate(config_tree, "yes")
    assert result.returncode != 0
    assert "must be true or false" in result.stdout
    assert not (config_tree / "docker/config/generated/gpu").exists()


def test_cpu_profile_unchanged(config_tree):
    result = generate(config_tree, "true", variant="cpu")
    assert result.returncode == 0, result.stdout + result.stderr
    config = properties(config_tree / "docker/config/generated/cpu/etc_worker/config_native.properties")
    assert not any(key.startswith("cudf.exchange_compression") for key in config)
