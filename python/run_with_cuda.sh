#!/usr/bin/env bash
set -e

SITE_PACKAGES=".venv/lib/python3.12/site-packages"

for library_dir in "$SITE_PACKAGES"/nvidia/*/lib; do
    if [[ -d "$library_dir" ]]; then
        export LD_LIBRARY_PATH="$library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
done

exec "$@"
