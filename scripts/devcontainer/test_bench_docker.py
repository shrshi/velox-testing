import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader("bench_docker", str(Path(__file__).with_name("bench-docker")))
spec = importlib.util.spec_from_loader(loader.name, loader)
bench = importlib.util.module_from_spec(spec)
loader.exec_module(bench)


class OwnershipTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.dict(os.environ, {"HOME": self.temp.name, "BENCH_DOCKER_OWNER": "alice"}))
        self.docker = self.enterContext(patch.object(bench, "docker"))
        self.container = "a" * 64
        self.other = "b" * 64
        self.docker.return_value = json.dumps([{
            "Id": self.container,
            "Config": {"Labels": {bench.OWNER_LABEL: "alice", bench.RUN_LABEL: "test"}},
        }])

    def run_command(self, *args):
        bench.main(["--run", "test", *args])

    def test_register_and_stop_by_full_id(self):
        self.run_command("register", "my-container")
        self.run_command("stop", self.container)
        self.docker.assert_called_with("container", "stop", self.container)

    def test_unregistered_container_cannot_be_removed(self):
        with self.assertRaises(ValueError):
            self.run_command("rm", self.container)
        self.docker.assert_not_called()

    def test_foreign_or_missing_labels_cannot_be_registered(self):
        for labels in ({}, {bench.OWNER_LABEL: "bob", bench.RUN_LABEL: "test"},
                       {bench.OWNER_LABEL: "alice", bench.RUN_LABEL: "different"}):
            self.docker.return_value = json.dumps([{"Id": self.container, "Config": {"Labels": labels}}])
            with self.assertRaises(ValueError):
                self.run_command("register", "foreign")

    def test_labels_rechecked_before_removal(self):
        self.run_command("register", "my-container")
        self.docker.return_value = json.dumps([{"Id": self.container, "Config": {"Labels": {}}}])
        with self.assertRaises(ValueError):
            self.run_command("rm", self.container)
        self.docker.assert_called_with("container", "inspect", self.container)

    def test_entire_batch_checked_before_mutation(self):
        self.run_command("register", "my-container")
        self.docker.reset_mock()
        with self.assertRaises(ValueError):
            self.run_command("stop", self.container, self.other)
        self.assertEqual(self.docker.call_count, 1)
        self.docker.assert_called_with("container", "inspect", self.container)

    def test_names_are_not_accepted_for_cleanup(self):
        self.run_command("register", "my-container")
        with self.assertRaises(ValueError):
            self.run_command("rm", "my-container")

    def test_path_traversal_run_rejected(self):
        with self.assertRaises(SystemExit):
            bench.main(["--run", "../../someone", "labels"])
        self.docker.assert_not_called()

    def test_ps_filters_owner_and_run(self):
        self.run_command("register", "my-container")
        self.docker.side_effect = [self.container, self.docker.return_value]
        self.run_command("ps")
        self.assertIn("label=org.velox-testing.bench.owner=alice", self.docker.call_args_list[-2].args)
        self.assertIn("label=org.velox-testing.bench.run=test", self.docker.call_args_list[-2].args)


if __name__ == "__main__":
    unittest.main()
