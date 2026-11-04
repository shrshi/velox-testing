#!/usr/bin/env python3
"""Launch-time RAM headroom; deliberately excludes swap and reclaim estimates."""

import re
import sys
from pathlib import Path, PurePosixPath

MIN_AVAILABLE_BYTES = 200_000_000_000  # 200 decimal GB, not GiB.


def nonnegative_integer(text):
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError(f"invalid memory counter: {text!r}")
    return int(text)


def available_bytes(proc=Path("/proc")):
    matches = re.findall(r"^MemAvailable:\s+([0-9]+) kB$", (proc / "meminfo").read_text(), re.M)
    if len(matches) != 1:
        raise ValueError("missing or malformed MemAvailable")
    available = int(matches[0]) * 1024  # Linux meminfo kB means KiB.
    memberships = []
    for line in (proc / "self/cgroup").read_text().splitlines():
        hierarchy, controllers, group = line.split(":", 2)
        if "memory" in controllers.split(","):
            memberships.append(("cgroup", group))
        elif hierarchy == "0" and not controllers:
            memberships.append(("cgroup2", group))
    # A controller belongs to only one hierarchy; hybrid systems may also list v2.
    memberships = [m for m in memberships if m[0] == "cgroup"] or memberships
    if not memberships:
        raise ValueError("cannot locate memory cgroup membership")

    mounts = []
    for line in (proc / "self/mountinfo").read_text().splitlines():
        fields, filesystem = line.split(" - ", 1)
        fields, filesystem = fields.split(), filesystem.split()
        if filesystem[0] in ("cgroup", "cgroup2"):
            # mountinfo encodes whitespace and backslashes as octal escapes.
            root, mount = [re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), p)
                           for p in fields[3:5]]
            if filesystem[0] == "cgroup2" or "memory" in filesystem[2].split(","):
                mounts.append((filesystem[0], PurePosixPath(root), Path(mount)))

    for kind, group in memberships:
        group = PurePosixPath(group)
        if not group.is_absolute() or ".." in group.parts:
            raise ValueError(f"invalid cgroup path: {group}")
        candidates = [(root, mount) for fs, root, mount in mounts
                      if fs == kind and group.is_relative_to(root)]
        if not candidates:
            raise ValueError(f"cannot locate {kind} memory mount for {group}")
        # Prefer the mount exposing the most ancestors, not a subtree bind mount.
        root, mount = min(candidates, key=lambda item: len(item[0].parts))
        current = mount / group.relative_to(root)
        limit_name, usage_name = (("memory.max", "memory.current") if kind == "cgroup2"
                                  else ("memory.limit_in_bytes", "memory.usage_in_bytes"))
        while True:
            try:
                limit = (current / limit_name).read_text().strip()
            except FileNotFoundError:
                if kind != "cgroup2" or current != mount or root != PurePosixPath("/"):
                    raise
                # Only the true v2 root omits cgroup.type (CFTYPE_NOT_ON_ROOT).
                # Namespace roots still have it, even when mountinfo says '/'.
                for name in ("cgroup.type", "memory.current"):
                    try:
                        (current / name).read_text()
                    except FileNotFoundError:
                        continue
                    raise ValueError(f"missing memory.max at non-root or incomplete cgroup: {current}")
                if "memory" not in (current / "cgroup.controllers").read_text().split():
                    raise ValueError(f"cannot verify root memory controller: {current}")
                break
            usage = nonnegative_integer((current / usage_name).read_text().strip())
            if not (kind == "cgroup2" and limit == "max"):
                available = min(available, max(0, nonnegative_integer(limit) - usage))
            if current == mount:
                break
            current = current.parent
    return available


def main():
    try:
        available = available_bytes()
        if available < MIN_AVAILABLE_BYTES:
            raise ValueError(f"only {available} bytes available")
    except (OSError, ValueError, IndexError) as error:
        print(f"Error: local compilation requires at least {MIN_AVAILABLE_BYTES} bytes "
              f"(200 decimal GB) available RAM: {error}", file=sys.stderr)
        return 1
    print(f"Local compilation: {available} bytes available RAM (launch-time check only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
