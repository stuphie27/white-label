#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHONPATH=. python -m compileall -q app tests scripts
PYTHONPATH=. python -m pytest -q
