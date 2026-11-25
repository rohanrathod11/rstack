#!/usr/bin/env bash
set -euo pipefail

test_dir=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export PATH="$test_dir/bin:$PATH"
exec python3 -B "$test_dir/test_scripts.py"
