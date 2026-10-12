#! /usr/bin/env bash
set -e

echo "Installing packer"
git clone --depth 1 https://github.com/wbthomason/packer.nvim /home/coder/.local/share/nvim/site/pack/packer/start/packer.nvim
