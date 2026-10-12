#! /usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -e

ARCH=$(uname -m)

echo "Installing essentials"
sudo DEBIAN_FRONTEND=noninteractive apt update
sudo DEBIAN_FRONTEND=noninteractive apt install -y file wget git build-essential gcc g++ gdb cmake make ninja-build curl openssh-client openssh-server kitty-terminfo

echo "Installing ripgrep and fd and jq"
sudo DEBIAN_FRONTEND=noninteractive apt install -y ripgrep fd-find jq

echo "Installing pipx"
sudo DEBIAN_FRONTEND=noninteractive apt install -y pipx

echo "Installing pre-commit"
if ! command -v pre-commit &>/dev/null; then
  pipx install pre-commit
  pipx ensurepath
fi
#echo "Pipx installing vectorcode"
#pipx install vectorcode --python python3.12

echo "Installing neovim"
mkdir -p /home/coder/neovim
if [[ $(uname -m) == "x86_64" ]]; then
  wget -P /home/coder/neovim https://github.com/neovim/neovim/releases/download/v0.11.6/nvim-linux-x86_64.appimage
  cd /home/coder/neovim
  chmod u+x nvim-linux-x86_64.appimage
  ./nvim-linux-x86_64.appimage --appimage-extract
  cd /usr/local/bin
  ln -s /home/coder/neovim/squashfs-root/usr/bin/nvim .
  cd
elif [[ $(uname -m) == "aarch64" ]]; then
  wget -P /home/coder/neovim https://github.com/neovim/neovim/releases/download/v0.11.6/nvim-linux-arm64.appimage
  cd /home/coder/neovim
  chmod u+x nvim-linux-arm64.appimage
  ./nvim-linux-arm64.appimage --appimage-extract
  cd /usr/local/bin
  ln -s /home/coder/neovim/squashfs-root/usr/bin/nvim .
  cd
fi

echo "Installing tmux"
TMUX_VERSION=3.7c
TMUX_ARCHIVE="/tmp/tmux-${TMUX_VERSION}.tar.gz"
TMUX_SOURCE="/tmp/tmux-${TMUX_VERSION}"

sudo DEBIAN_FRONTEND=noninteractive apt install -y \
  build-essential bison libevent-dev libncurses-dev pkg-config

curl -fsSLo "$TMUX_ARCHIVE" \
  "https://github.com/tmux/tmux/releases/download/${TMUX_VERSION}/tmux-${TMUX_VERSION}.tar.gz"

tar -xzf "$TMUX_ARCHIVE" -C /tmp
(
  cd "$TMUX_SOURCE"
  ./configure
  make -j"$(nproc)"
  sudo make install
)

test "$(/usr/local/bin/tmux -V)" = "tmux ${TMUX_VERSION}"
rm -rf "$TMUX_SOURCE" "$TMUX_ARCHIVE"
# TODO: how is this install different from bind mounting .tmux
# Is there a better tmux configuration tool out there? tmux-config is not being maintained
# Rest moved to post-create.sh

echo "Installing clangd"
sudo DEBIAN_FRONTEND=noninteractive apt install -y software-properties-common gnupg lsb-release
LLVM_VERSION=20
wget https://apt.llvm.org/llvm.sh
chmod +x llvm.sh
./llvm.sh ${LLVM_VERSION}
sudo DEBIAN_FRONTEND=noninteractive apt install -y clangd-${LLVM_VERSION}
update-alternatives --install /usr/bin/clangd clangd /usr/bin/clangd-${LLVM_VERSION} 100
rm llvm.sh

echo "Installing nsight systems"
sudo DEBIAN_FRONTEND=noninteractive apt install -y libxi6 libxcb-dri2-0
NSIGHT_SYSTEMS_VERSION=2026.5.1
NSIGHT_SYSTEMS_BUILD=2026.5.1.161-1
NSIGHT_SYSTEMS_PACKAGE="nsight-systems-${NSIGHT_SYSTEMS_VERSION}_${NSIGHT_SYSTEMS_BUILD}_amd64.deb"
temp_deb="$(mktemp)" &&
  wget -O "$temp_deb" "https://developer.download.nvidia.com/devtools/repos/ubuntu2404/amd64/${NSIGHT_SYSTEMS_PACKAGE}" &&
  sudo dpkg -i "$temp_deb" &&
  rm -f "$temp_deb"
mkdir -p /home/coder/.local/bin
ln -sf "/opt/nvidia/nsight-systems/${NSIGHT_SYSTEMS_VERSION}/target-linux-x64/nsys" /home/coder/.local/bin/nsys

echo "Installing bash-completion"
sudo DEBIAN_FRONTEND=noninteractive apt install -y bash-completion

echo "Installing nodejs"
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo DEBIAN_FRONTEND=noninteractive apt install -y nodejs

echo "Installing opencode"
sudo npm install -g opencode-ai@latest

echo "Installing pi"
npm install -g --ignore-scripts @earendil-works/pi-coding-agent

echo "Installing GitHub CLI (gh)"
if ! command -v gh &>/dev/null; then
  GH_VERSION="2.88.0"
  curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" | tar -xz -C /usr/local
  ln -sf "/usr/local/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh
fi

# Clean up apt cache to reduce image size
rm -rf /var/lib/apt/lists/*

# install.sh runs as root during image build but installs into /home/coder/. Those files will be root-owned.
# So we need explicity chown
chown -R coder:coder /home/coder/
