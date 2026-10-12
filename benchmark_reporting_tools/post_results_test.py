# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime, timezone

from benchmark_reporting_tools.post_results import BenchmarkMetadata, BenchmarkResults, _build_submission_payload


def test_submission_preserves_validation_and_failed_queries():
    metadata = BenchmarkMetadata(benchmark=["tpch"], timestamp=datetime.now(timezone.utc), engine="presto")
    results = BenchmarkResults("tpch", {"Q1": [10.0], "Q2": None}, {"Q2": "failed", "Q3": "failed"})
    args = dict(
        benchmark_metadata=metadata,
        benchmark_results=results,
        engine_config=None,
        sku_name="test",
        storage_configuration_name="test",
        benchmark_definition_name="tpch",
        cache_state="cold",
        engine_name=None,
        identifier_hash="test",
        version=None,
        commit_hash=None,
        is_official=False,
    )
    payload = _build_submission_payload(
        **args,
        validation_results={"overall_status": "passed", "queries": {"q1": {"status": "passed"}}},
    )
    assert payload["validation_status"] == "passed"
    assert payload["query_logs"][0]["validation_result"]["status"] == "passed"
    for query in payload["query_logs"][1:]:
        assert query["status"] == "error"
        assert query["runtime_ms"] is None
        assert query["validation_result"]["status"] == "not-validated"
    assert _build_submission_payload(**args)["validation_status"] == "not-validated"
