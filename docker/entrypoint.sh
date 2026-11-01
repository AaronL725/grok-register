#!/bin/sh
set -eu
umask 077

config_file="\${GROK_REGISTER_CONFIG_FILE:-/app/config/config.json}"
if [ ! -f "$config_file" ]; then
    mkdir -p "$(dirname "$config_file")"
    cp /app/config.docker.example.json "$config_file"
    chmod 600 "$config_file"
fi

exec "$@"
