# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("readiness", Path(__file__).with_name("gpu_worker_readiness.py"))
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.endpoints = [("http://172.20.0.2:10020", "http://presto-native-worker-gpu-2:10020")]
        self.responses = {
            "http://localhost:8080/v1/cluster?includeLocalInfoOnly=true": {"activeWorkers": 1},
            "http://localhost:8080/v1/node": [{"uri": self.endpoints[0][0] + "/v1/status"}],
            self.endpoints[0][0] + "/v1/info/state": "ACTIVE",
        }

    def check(self):
        readiness.check_readiness("http://localhost:8080", self.endpoints, self.responses.__getitem__)

    def test_healthy_endpoints(self):
        self.check()

    def test_stale_or_duplicate_inventory(self):
        for nodes in (
            [{"uri": "http://172.20.0.22:10020"}],
            [{"uri": self.endpoints[0][0]}] * 2,
            [],
        ):
            with self.subTest(nodes=nodes):
                self.responses["http://localhost:8080/v1/node"] = nodes
                with self.assertRaises(ValueError):
                    self.check()

    def test_health_and_active_count_required(self):
        for count in (0, 2, True, None):
            self.responses["http://localhost:8080/v1/cluster?includeLocalInfoOnly=true"] = {"activeWorkers": count}
            with self.assertRaises(ValueError):
                self.check()
        self.responses["http://localhost:8080/v1/cluster?includeLocalInfoOnly=true"] = {"activeWorkers": 1}
        self.responses[self.endpoints[0][0] + "/v1/info/state"] = "STARTING"
        with self.assertRaises(ValueError):
            self.check()

    def test_single_container_noncontiguous_ids(self):
        container = {
            "State": {"Status": "running"},
            "NetworkSettings": {"Networks": {"default": {"IPAddress": "172.20.0.2"}}},
        }
        with patch.object(readiness.subprocess, "check_output", return_value=json.dumps([container])):
            endpoints = readiness.inspect_workers([2, 5], True)
        self.assertEqual(
            [endpoint[0] for endpoint in endpoints], ["http://172.20.0.2:10020", "http://172.20.0.2:10050"]
        )
        container["State"] = {"Status": "exited", "ExitCode": 1}
        with patch.object(readiness.subprocess, "check_output", return_value=json.dumps([container])):
            with self.assertRaises(readiness.WorkerExitedError):
                readiness.inspect_workers([2], False)

    def test_two_consecutive_checks_and_timeout(self):
        for results, timeout, expected_checks, success in (
            ([None, None], 20, 2, True),
            ([None, ValueError("starting"), None, None], 20, 4, True),
            ([None], 3, 1, False),
        ):
            with self.subTest(results=results, timeout=timeout):
                clock = [0]

                def sleep(seconds):
                    clock[0] += seconds

                with (
                    patch.object(readiness.time, "monotonic", side_effect=lambda: clock[0]),
                    patch.object(readiness.time, "sleep", side_effect=sleep),
                    patch.object(readiness, "inspect_workers", return_value=self.endpoints),
                    patch.object(readiness, "check_readiness", side_effect=results) as check,
                ):
                    if success:
                        readiness.wait_for_workers("http://localhost:8080", [2], False, timeout)
                    else:
                        with self.assertRaises(TimeoutError):
                            readiness.wait_for_workers("http://localhost:8080", [2], False, timeout)
                    self.assertEqual(check.call_count, expected_checks)


if __name__ == "__main__":
    unittest.main()
