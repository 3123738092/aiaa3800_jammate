#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${MRT2_PYTHON:-python}" -m mrt2_demo.run --seconds 60 --mode midi --play --interactive --out mrt2_demo/results/live "$@"
