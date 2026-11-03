#!/usr/bin/env sh
set -eu
exec uv run --extra app fin-graph app "$@"
