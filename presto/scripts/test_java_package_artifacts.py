# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION.
# SPDX-License-Identifier: Apache-2.0

"""Run the Java packaging shell with mocked Docker, Maven and installers."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("build_presto_java_package.sh")


@pytest.mark.parametrize("clean", ["true", "false"])
def test_copies_current_version_with_stale_artifacts(tmp_path, clean):
    host_bin = tmp_path / "host-bin"
    container_bin = tmp_path / "container-bin"
    host_bin.mkdir()
    container_bin.mkdir()

    def executable(path, content):
        path.write_text(content)
        path.chmod(0o755)

    command_file = tmp_path / "container-command"
    executable(host_bin / "docker", '#!/bin/bash\nprintf "%s" "${@: -1}" > "$COMMAND_FILE"\n')
    env = {
        **os.environ,
        "PATH": f"{host_bin}:{os.environ['PATH']}",
        "PRESTO_VERSION": "testing",
        "PRESTO_DEV_JAVA_CLEAN": clean,
        "COMMAND_FILE": str(command_file),
    }
    subprocess.run(["bash", str(SCRIPT)], env=env, check=True, capture_output=True, text=True)
    for tool in ("apt-get", "git", "curl", "npm"):
        executable(container_bin / tool, "#!/bin/bash\nexit 0\n")
    executable(
        tmp_path / "mvnw",
        """#!/bin/bash
printf '%s\n' "$*" >> maven-calls
if [[ " $* " == *" help:evaluate "* ]]; then
  printf '0.300-SNAPSHOT'
fi
""",
    )
    (tmp_path / "docker").mkdir()
    for module, suffix in (
        ("presto-server", ".tar.gz"),
        ("presto-function-server", "-executable.jar"),
        ("presto-cli", "-executable.jar"),
    ):
        target = tmp_path / module / "target"
        target.mkdir(parents=True)
        for version in ("0.299-SNAPSHOT", "0.300-SNAPSHOT"):
            (target / f"{module}-{version}{suffix}").write_text(version)
    env["PATH"] = f"{container_bin}:{os.environ['PATH']}"
    subprocess.run(["bash", str(command_file)], cwd=tmp_path, env=env, check=True, capture_output=True, text=True)
    outputs = sorted((tmp_path / "docker").iterdir())
    assert {path.name for path in outputs} == {
        "presto-server-testing.tar.gz",
        "presto-function-server-executable.jar",
        "presto-function-server-testing-executable.jar",
        "presto-cli-testing-executable.jar",
    }
    assert all(path.read_text() == "0.300-SNAPSHOT" for path in outputs)
    calls = (tmp_path / "maven-calls").read_text().splitlines()
    assert ("clean" in calls[0].split()) == (clean == "true")
    assert "-Daether.connector.http.retryHandler.serviceUnavailable=429,500,502,503,504" in calls[0]
    assert "-Daether.connector.http.retryHandler.count.gcs-maven-central-mirror=0" in calls[0]
    assert "-Dexpression=project.version" in calls[1]
