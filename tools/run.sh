#!/bin/sh
set -eu
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v uv >/dev/null 2>&1; then
    uv_bin=$(command -v uv)
else
    uv_bin="$bundle_dir/.tools/uv"
    if [ ! -x "$uv_bin" ]; then
        echo "安装 uv 到本目录 .tools（不修改 shell 配置）…"
        mkdir -p "$bundle_dir/.tools"
        curl -LsSf https://astral.sh/uv/install.sh -o "$bundle_dir/.tools/install-uv.sh"
        UV_UNMANAGED_INSTALL="$bundle_dir/.tools" sh "$bundle_dir/.tools/install-uv.sh"
    fi
fi
device_script="$bundle_dir/device.py"
if [ -f "$bundle_dir/tools/device.py" ]; then device_script="$bundle_dir/tools/device.py"; fi
exec "$uv_bin" run --no-project --python 3.12 --with esptool==5.6.0 --with pyserial==3.5 "$device_script" "$@"
