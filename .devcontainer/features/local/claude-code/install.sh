#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -e

echo "Installing bubblewrap and socat (Claude Code dependencies)"
sudo DEBIAN_FRONTEND=noninteractive apt update
sudo DEBIAN_FRONTEND=noninteractive apt install -y bubblewrap socat
rm -rf /var/lib/apt/lists/*

echo "Installing Claude Code"
su - coder -c 'curl -fsSL https://claude.ai/install.sh | bash'

echo "Adding ~/.local/bin to PATH in .bashrc"
grep -q 'export PATH="$HOME/.local/bin:$PATH"' /home/coder/.bashrc || \
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> /home/coder/.bashrc

chown -R coder:coder /home/coder/.local
