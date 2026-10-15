# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from common.testing.performance_benchmarks.benchmark_keys import BenchmarkKeys
from presto.testing.performance_benchmarks import common_fixtures, metrics_collector, run_context


def request_for(tmp_path, **overrides):
    options = {
        "--iterations": 2,
        "--profile": False,
        "--profile-script-path": None,
        "--metrics": True,
        "--output-dir": str(tmp_path),
        "--tag": None,
        "--hostname": "coordinator",
        "--port": 8080,
        "--cache-mode": "off",
        "--connector-id": "hive",
        "--skip-drop-cache": True,
        "--session-property": ["native_cudf_exchange_enabled=true", "query_priority=2"],
        "--user": "test",
        "--schema-name": "sf3000",
        "--scale-factor": "3000",
    }
    options.update(overrides)
    return SimpleNamespace(
        config=SimpleNamespace(getoption=lambda key: options[key]),
        node=SimpleNamespace(obj=SimpleNamespace(BENCHMARK_TYPE="tpch")),
        session=SimpleNamespace(),
    )


class Cursor:
    def __init__(self, failure=None):
        self.failure = failure
        self._query = SimpleNamespace(query_id="old-query")
        self.stats = {"elapsedTimeMillis": 17}
        self.statements = []

    def execute(self, query):
        self.statements.append(query)
        if not query.startswith("DROP"):
            self._query = SimpleNamespace(query_id=f"query-{len(self.statements)}")
            if self.failure:
                raise self.failure
        return self

    def fetchall(self):
        return [(1,)]


def test_session_properties_reach_connection_and_metadata(monkeypatch, tmp_path):
    request = request_for(tmp_path)
    connections = []
    monkeypatch.setattr(
        common_fixtures.prestodb.dbapi,
        "connect",
        lambda **kwargs: connections.append(kwargs) or SimpleNamespace(cursor=lambda: None),
    )
    common_fixtures.presto_cursor.__wrapped__(request)
    assert connections[0]["session_properties"] == {"native_cudf_exchange_enabled": "true", "query_priority": "2"}
    contexts = []
    monkeypatch.setattr(common_fixtures, "gather_run_context", lambda **kwargs: contexts.append(kwargs) or {})
    fixture = common_fixtures.run_context_collector.__wrapped__(request)
    ctx = next(fixture)
    assert ctx["session_properties"] == connections[0]["session_properties"]
    assert contexts[0]["scale_factor"] == "3000"
    with pytest.raises(StopIteration):
        next(fixture)
    assert request.session.run_context == ctx


@pytest.mark.parametrize("value", ["bad", "=true", "   =true"])
def test_invalid_session_properties(tmp_path, value):
    with pytest.raises(pytest.UsageError, match="key=value"):
        common_fixtures.session_properties_from_config(request_for(tmp_path, **{"--session-property": [value]}).config)


@pytest.mark.parametrize("scale_factor, expected", [("3000", 3000), ("0.5", 0.5), (None, 100), ("nan", 100)])
def test_explicit_scale_factor_preserves_other_metadata(monkeypatch, scale_factor, expected):
    monkeypatch.setattr(run_context, "_get_schema_info", lambda *args: {"scale_factor": 100, "data_dir": "s3://data"})
    monkeypatch.setattr(run_context, "_get_node_count", lambda *args: 4)
    monkeypatch.setattr(run_context, "_get_engine", lambda *args: "presto-java")
    monkeypatch.setattr(run_context, "_get_num_drivers", lambda: None)
    monkeypatch.setattr(run_context, "_get_image_provenance", lambda: {"velox_sha": "abc"})
    ctx = run_context.gather_run_context("host", 8080, "user", "sf3000", scale_factor)
    assert ctx["scale_factor"] == expected
    assert ctx["data_dir"] == "s3://data"
    assert ctx["velox_sha"] == "abc"


@pytest.mark.parametrize("ctas", [False, True])
@pytest.mark.parametrize("metrics_fail", [False, True])
def test_failed_queries_collect_metrics_without_masking_error(monkeypatch, tmp_path, ctas, metrics_fail):
    error = RuntimeError("query failed")
    cursor = Cursor(error)
    collected = []

    def collect(**kwargs):
        collected.append(kwargs["query_id"])
        if metrics_fail:
            raise OSError("metrics failed")

    monkeypatch.setattr(common_fixtures, "collect_metrics", collect)
    results = {}
    run = common_fixtures.benchmark_query.__wrapped__(
        request_for(tmp_path),
        cursor,
        {"Q18": "SELECT 1"},
        results,
        SimpleNamespace(catalog="hive_output", schema="results") if ctas else None,
    )
    with pytest.raises(RuntimeError, match="query failed") as raised:
        run("Q18")
    assert raised.value is error
    assert collected == ["query-1"]
    assert results["tpch"][BenchmarkKeys.RAW_TIMES_KEY]["Q18"] is None


@pytest.mark.parametrize("ctas", [False, True])
def test_successful_iterations_and_ctas_cleanup(monkeypatch, tmp_path, ctas):
    cursor = Cursor()
    collected = []
    monkeypatch.setattr(common_fixtures, "collect_metrics", lambda **kwargs: collected.append(kwargs["query_id"]))
    monkeypatch.setattr(common_fixtures, "write_query_result", lambda *args: None)
    results = {}
    run = common_fixtures.benchmark_query.__wrapped__(
        request_for(tmp_path),
        cursor,
        {"Q18": "SELECT 1"},
        results,
        SimpleNamespace(catalog="hive_output", schema="results") if ctas else None,
    )
    run("Q18")
    assert collected == ["query-1", "query-2"]
    assert results["tpch"][BenchmarkKeys.RAW_TIMES_KEY]["Q18"] == [17, 17]
    if ctas:
        assert cursor.statements[-1] == "DROP TABLE IF EXISTS hive_output.results.q18_iteration_2"


def test_ctas_fetch_failure_collects_before_cleanup(monkeypatch, tmp_path):
    cursor = Cursor()
    events = []

    def fetchall():
        if len(cursor.statements) == 2:
            raise RuntimeError("fetch failed")
        return [(1,)]

    cursor.fetchall = fetchall
    monkeypatch.setattr(common_fixtures, "collect_metrics", lambda **kwargs: events.append(kwargs["query_id"]))
    run = common_fixtures.benchmark_query.__wrapped__(
        request_for(tmp_path),
        cursor,
        {"Q18": "SELECT 1"},
        {},
        SimpleNamespace(catalog="hive_output", schema="results"),
    )
    with pytest.raises(RuntimeError, match="fetch failed"):
        run("Q18")
    assert events == ["query-1", "query-2"]
    assert cursor.statements[-1] == "DROP TABLE IF EXISTS hive_output.results.q18_iteration_2"


def test_submit_failure_does_not_collect_previous_query(monkeypatch, tmp_path):
    cursor = Cursor()

    def fail_submit(query):
        raise RuntimeError("connection failed before query submission")

    cursor.execute = fail_submit
    monkeypatch.setattr(common_fixtures, "collect_metrics", lambda **kwargs: pytest.fail("stale metrics"))
    run = common_fixtures.benchmark_query.__wrapped__(request_for(tmp_path), cursor, {"Q18": "SELECT 1"}, {}, None)
    with pytest.raises(RuntimeError, match="connection failed"):
        run("Q18")


def test_pre_query_failure_does_not_collect_stale_metrics(monkeypatch, tmp_path):
    def fail_reset(*args):
        raise RuntimeError("cache reset failed")

    monkeypatch.setattr(common_fixtures, "cold_run_reset", fail_reset)
    monkeypatch.setattr(common_fixtures, "collect_metrics", lambda **kwargs: pytest.fail("stale metrics"))
    run = common_fixtures.benchmark_query.__wrapped__(
        request_for(tmp_path, **{"--cache-mode": "cold"}),
        Cursor(),
        {"Q18": "SELECT 1"},
        {},
        None,
    )
    with pytest.raises(RuntimeError, match="cache reset failed"):
        run("Q18")


@pytest.mark.parametrize("benchmark_exit, validation_exit, expected", [(0, 0, 0), (1, 2, 1), (0, 2, 2)])
def test_dev_wrapper_preserves_arguments_artifacts_and_exit_status(tmp_path, benchmark_exit, validation_exit, expected):
    repo = Path(__file__).resolve().parents[2]
    scripts = tmp_path / "presto/scripts"
    scripts.mkdir(parents=True)
    for name in ("run_benchmark.sh", "run_benchmark_dev.sh", "presto_connection_defaults.sh"):
        shutil.copy(repo / "presto/scripts" / name, scripts / name)
    (scripts / "common_functions.sh").write_text("wait_for_worker_node_registration() { :; }\n")
    helpers = tmp_path / "scripts"
    helpers.mkdir()
    (helpers / "dev_python_env.sh").write_text("dev_python_activate() { :; }\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    pytest_stub = binaries / "pytest"
    pytest_stub.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        "open(os.environ['CAPTURE_ARGS'], 'w').write(json.dumps(sys.argv[1:]))\n"
        "sys.exit(int(os.environ['BENCHMARK_EXIT']))\n"
    )
    python_stub = binaries / "python"
    python_stub.write_text('#!/bin/bash\nexit "$STUB_VALIDATION_EXIT"\n')
    pytest_stub.chmod(0o755)
    python_stub.chmod(0o755)
    logs = scripts / "presto_logs"
    logs.mkdir()
    (logs / "worker.log").write_text("diagnostic")
    configs = tmp_path / "presto/docker/config/generated"
    configs.mkdir(parents=True)
    (configs / "config.properties").write_text("config")
    output = tmp_path / "results with spaces"
    captured = tmp_path / "args.json"
    result = subprocess.run(
        [
            str(scripts / "run_benchmark_dev.sh"),
            "-b",
            "tpch",
            "-s",
            "sf3000",
            "-o",
            str(output),
            "-t",
            "trial",
            "--scale-factor",
            "3000",
            "--session-property",
            "native_cudf_exchange_enabled=true",
            "--session-property",
            "application_name=two words",
            "--cache-mode",
            "hot",
            "--warmup-iterations",
            "0",
        ],
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "CAPTURE_ARGS": str(captured),
            "BENCHMARK_EXIT": str(benchmark_exit),
            "STUB_VALIDATION_EXIT": str(validation_exit),
            "LOGS_DIR": str(logs),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    args = json.loads(captured.read_text())
    assert args.count("--session-property") == 2
    assert "application_name=two words" in args
    assert args[args.index("--scale-factor") + 1] == "3000"
    assert args[args.index("--cache-mode") + 1] == "hot"
    assert (output / "trial/logs/worker.log").read_text() == "diagnostic"
    assert (output / "trial/config/config.properties").read_text() == "config"


def test_worker_uri_rewrites_preserve_port_and_path(monkeypatch):
    monkeypatch.setattr(metrics_collector, "_load_worker_host_map", lambda: {"worker": "::1"})
    resolver = metrics_collector.WorkerUriResolver()
    assert resolver.resolve("http://worker:7777/v1/task/a") == "http://[::1]:7777/v1/task/a"
    assert resolver.resolve("http://localhost:8080") == "http://localhost:8080"


def test_worker_uri_docker_lookup_is_cached(monkeypatch):
    monkeypatch.setattr(metrics_collector, "_load_worker_host_map", lambda: {})
    calls = []
    monkeypatch.setattr(metrics_collector, "_docker_container_ip", lambda host: calls.append(host) or "172.18.0.4")
    resolver = metrics_collector.WorkerUriResolver()
    for _ in range(2):
        assert resolver.resolve("http://presto-native-worker-gpu-3:7777") == "http://172.18.0.4:7777"
    assert calls == ["presto-native-worker-gpu-3"]


def test_explicit_worker_host_map_overrides_generated_compose(monkeypatch):
    monkeypatch.setenv("PRESTO_BENCHMARK_WORKER_HOST_MAP", "presto-native-worker-gpu-3=127.0.0.1")
    monkeypatch.setattr(
        metrics_collector,
        "_load_worker_host_map_from_compose",
        lambda path: {"presto-native-worker-gpu-3": "172.18.0.4"},
    )
    assert metrics_collector._load_worker_host_map()["presto-native-worker-gpu-3"] == "127.0.0.1"
