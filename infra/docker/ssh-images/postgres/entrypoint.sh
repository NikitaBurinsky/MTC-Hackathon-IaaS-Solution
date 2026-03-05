#!/bin/sh
set -eu

if [ -z "${SSH_USER:-}" ] || [ -z "${SSH_PASSWORD:-}" ]; then
    echo "SSH_USER and SSH_PASSWORD are required" >&2
    exit 1
fi

if ! id -u "$SSH_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$SSH_USER"
fi

echo "$SSH_USER:$SSH_PASSWORD" | chpasswd
ssh-keygen -A

/usr/sbin/sshd
exec docker-entrypoint.sh "$@"
