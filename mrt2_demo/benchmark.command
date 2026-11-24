#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${MRT2_PYTHON:-python}" -m mrt2_demo.benchmark
