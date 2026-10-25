#!/bin/sh
set -eu

if [ ! -w /data ]; then
    echo "ERROR: /data is not writable by the app (user id $(id -u))." >&2
    echo "Mount a Docker volume there, or make the folder writable for that user." >&2
    exit 1
fi

cd /data
mkdir -p avatars knowledge meetings brand logs .fastembed_cache
[ -f presets.json ] || echo '{"presets": []}' > presets.json

cd /app
exec "$@"
