#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -e

echo "Installing Goose"
su - coder -c 'curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | CONFIGURE=false bash'

chown -R coder:coder /home/coder/.local
