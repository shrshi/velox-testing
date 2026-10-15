#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION.
# SPDX-License-Identifier: Apache-2.0

"""Wait for the expected GPU containers and coordinator to agree on readiness."""

import argparse
import json
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit


class WorkerExitedError(RuntimeError):
    pass


def inspect_workers(gpu_ids, single_container):
    services = (
        ["presto-native-worker-gpu"]
        if single_container or len(gpu_ids) == 1
        else [f"presto-native-worker-gpu-{gpu_id}" for gpu_id in gpu_ids]
    )
    containers = json.loads(subprocess.check_output(["docker", "inspect", *services], text=True, timeout=3))
    endpoints = []
    for service, container in zip(services, containers, strict=True):
        state = container["State"]["Status"]
        if state in {"exited", "dead"}:
            raise WorkerExitedError(
                f"{service} is {state}, exit code {container['State'].get('ExitCode')}; inspect docker logs"
            )
        if state != "running":
            raise ValueError(f"{service} is {state}")
        networks = container["NetworkSettings"]["Networks"]
        addresses = [network["IPAddress"] for network in networks.values() if network.get("IPAddress")]
        if len(addresses) != 1:
            raise ValueError(f"{service}: expected one Docker network address, got {addresses}")
        ids = gpu_ids if len(services) == 1 else [gpu_ids[services.index(service)]]
        for gpu_id in ids:
            port = int(f"10{gpu_id:02d}0")
            endpoints.append((f"http://{addresses[0]}:{port}", f"http://{service}:{port}"))
    return endpoints


def normalize_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
        raise ValueError(f"Invalid worker URI: {url}")
    return f"http://{parsed.hostname}:{parsed.port}"


def check_readiness(coordinator, endpoints, get_json):
    cluster = get_json(f"{coordinator}/v1/cluster?includeLocalInfoOnly=true")
    active = cluster.get("activeWorkers")
    if type(active) is not int or active != len(endpoints):
        raise ValueError(f"Coordinator reports {active!r} ACTIVE workers; expected {len(endpoints)}")
    nodes = get_json(f"{coordinator}/v1/node")
    if not isinstance(nodes, list) or len(nodes) != len(endpoints):
        raise ValueError("Coordinator discovery inventory has the wrong worker count")
    observed = {normalize_url(node["uri"]) for node in nodes}
    if len(observed) != len(endpoints) or any(not observed.intersection(aliases) for aliases in endpoints):
        raise ValueError(f"Coordinator discovery endpoints do not match current containers: {sorted(observed)}")
    for direct, _ in endpoints:
        state = get_json(f"{direct}/v1/info/state")
        if state != "ACTIVE":
            raise ValueError(f"Worker {direct} is {state!r}, not ACTIVE")


def wait_for_workers(coordinator, gpu_ids, single_container, timeout):
    deadline = time.monotonic() + timeout
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get_json(url):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Readiness deadline expired")
        with http.open(url, timeout=min(3, remaining)) as response:
            return json.load(response)

    consecutive = 0
    last_error = "No readiness checks completed"
    while time.monotonic() < deadline:
        try:
            endpoints = inspect_workers(gpu_ids, single_container)
            check_readiness(coordinator, endpoints, get_json)
            consecutive += 1
            if consecutive >= 2 and time.monotonic() < deadline:
                print(f"All {len(gpu_ids)} GPU workers healthy and coordinator-ACTIVE on two consecutive polls.")
                return
        except WorkerExitedError:
            raise
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            consecutive = 0
            last_error = str(error)
            print(f"Waiting for GPU workers: {error}", flush=True)
        time.sleep(max(0, min(5, deadline - time.monotonic())))
    raise TimeoutError(f"Workers did not remain ready within {timeout}s: {last_error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu-ids", required=True)
    parser.add_argument("--single-container", choices=["true", "false"], default="false")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--coordinator", default="http://localhost:8080")
    args = parser.parse_args()
    gpu_ids = [int(value) for value in args.gpu_ids.split(",")]
    if args.timeout <= 0 or any(gpu_id < 0 for gpu_id in gpu_ids) or len(set(gpu_ids)) != len(gpu_ids):
        parser.error("Timeout must be positive and GPU IDs must be unique nonnegative integers")
    wait_for_workers(args.coordinator, gpu_ids, args.single_container == "true", args.timeout)


if __name__ == "__main__":
    main()
