#!/usr/bin/env bash
set -e

# Try to load kernel modules if host allows (NET_ADMIN / SYS_MODULE)
modprobe wireguard 2>/dev/null || true
modprobe amneziawg 2>/dev/null || true

# Prepare runtime directories if missing
mkdir -p /etc/wireguard /etc/amnezia/amneziawg /opt/wg-auto/releases /opt/wg-auto/awg/bin

if [ "$1" = "--test" ]; then
    shift
    exec bash /app/test_e2e_real_tunnel.sh /opt/diana-awg/bin "$@"
fi

if [ $# -eq 0 ]; then
    exec python3 /app/panel.py
fi

exec "$@"
