#!/usr/bin/env bash
# Build the native capture helper (helper/build/dscope_helper).
#   macOS:  brew install cmake pkg-config glib libusb
#   Linux:  sudo apt install cmake pkg-config libglib2.0-dev libusb-1.0-0-dev zlib1g-dev
# Downloads DSView's source the first time (needs git and internet).
set -euo pipefail
cd "$(dirname "$0")"
cmake -S helper -B helper/build -DCMAKE_BUILD_TYPE=Release "$@"
cmake --build helper/build -j 4
echo
echo "Built: $(pwd)/helper/build/dscope_helper"
