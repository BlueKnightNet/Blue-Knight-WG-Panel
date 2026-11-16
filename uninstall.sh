#!/usr/bin/env bash
set -Eeuo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: uninstall.sh [OPTIONS]

Uninstall Blue Knight WG Panel, systemd units, network interfaces, and configuration files.

Options:
  -h, --help      Display this help message and exit.
  -y, --yes       Execute uninstallation in non-interactive mode.
  -f, --force     Alias for -y / --yes.
  --purge         Remove all configuration files, keys, and client credentials.
  --dry-run       Simulate uninstallation actions without making system changes.

Examples:
  sudo ./uninstall.sh
  sudo ./uninstall.sh --yes --purge
  ./uninstall.sh --dry-run
EOF
}

non_interactive=0
purge_mode=0
purge_explicit=0
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -y|--yes|-f|--force)
      non_interactive=1
      shift
      ;;
    --purge)
      purge_mode=1
      purge_explicit=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    *)
      die "Unknown option: $1. Use --help to view available options."
      ;;
  esac
done

if (( dry_run == 1 )); then
  printf '[DRY-RUN] Simulation mode enabled. No system modifications will occur.\n'
  printf '[DRY-RUN] Stop and disable systemd services:\n'
  printf '          - wg-auto-panel.service\n'
  printf '          - wg-auto-awg.service\n'
  printf '          - wg-quick@wgauto0.service\n'
  printf '          - wg-quick@warp0.service\n'
  printf '[DRY-RUN] Remove network interfaces and routing policies:\n'
  printf '          - Interface wgauto0\n'
  printf '          - Interface awgauto0\n'
  printf '          - Interface warp0\n'
  printf '          - IP rule pref 14500\n'
  printf '          - IP rule pref 15000\n'
  printf '          - Route table 100\n'
  printf '[DRY-RUN] Remove panel firewall rules (TCP port 8787).\n'
  printf '[DRY-RUN] Remove systemd service unit files:\n'
  printf '          - /etc/systemd/system/wg-auto-panel.service\n'
  printf '          - /etc/systemd/system/wg-auto-awg.service\n'
  printf '[DRY-RUN] Remove installation files and symlinks:\n'
  printf '          - /usr/local/sbin/wg-auto-update\n'
  printf '          - /opt/wg-auto\n'
  printf '          - /etc/sysctl.d/90-wg-auto.conf\n'
  if (( purge_mode == 1 )); then
    printf '[DRY-RUN] Purge configuration directories and credentials:\n'
    printf '          - /etc/wireguard\n'
    printf '          - /etc/amnezia\n'
    printf '          - /root/wireguard-client.conf\n'
  else
    printf '[DRY-RUN] Purge mode not set; configuration files would be retained or prompted.\n'
  fi
  printf '[DRY-RUN] Remove process locks (/run/wg-auto-*.lock).\n'
  printf '[DRY-RUN] Uninstallation simulation completed successfully.\n'
  exit 0
fi

[[ $EUID -eq 0 ]] || die 'Run this uninstaller with sudo bash.'

if (( non_interactive == 0 )) && [[ -t 0 ]]; then
  printf 'Blue Knight WG Panel Uninstaller\n'
  printf 'This operation removes panel services, network interfaces, and binaries.\n'
  read -r -p "Do you want to proceed with uninstallation? [y/N]: " confirm_uninstall || true
  case "${confirm_uninstall:-}" in
    [yY]|[yY][eE][sS]) ;;
    *)
      printf 'Uninstallation cancelled by user.\n'
      exit 0
      ;;
  esac

  if (( purge_explicit == 0 )); then
    read -r -p "Do you want to purge all WireGuard keys, configs, and client credentials? [y/N]: " confirm_purge || true
    case "${confirm_purge:-}" in
      [yY]|[yY][eE][sS])
        purge_mode=1
        ;;
      *)
        purge_mode=0
        ;;
    esac
  fi
else
  # Default to full cleanup when non-interactive and --purge is not explicitly overridden
  if (( purge_explicit == 0 )); then
    purge_mode=1
  fi
fi

printf 'Stopping and disabling systemd services...\n'
systemctl disable --now wg-auto-panel.service wg-auto-awg.service 2>/dev/null || true
systemctl disable --now "wg-quick@wgauto0.service" 2>/dev/null || true
systemctl disable --now "wg-quick@warp0" 2>/dev/null || true

printf 'Tearing down network interfaces and routing rules...\n'
ip link delete wgauto0 2>/dev/null || true
ip link delete awgauto0 2>/dev/null || true
ip link delete warp0 2>/dev/null || true
ip rule del pref 14500 2>/dev/null || true
ip rule del pref 15000 2>/dev/null || true
ip route flush table 100 2>/dev/null || true

printf 'Removing firewall rules...\n'
iptables -D INPUT -p tcp --dport 8787 -m comment --comment wg-auto-panel -j ACCEPT 2>/dev/null || true

printf 'Removing systemd unit definitions...\n'
rm -f /etc/systemd/system/wg-auto-panel.service
rm -f /etc/systemd/system/wg-auto-awg.service
systemctl daemon-reload 2>/dev/null || true

printf 'Removing application directories and configuration symlinks...\n'
rm -f /usr/local/sbin/wg-auto-update
rm -rf /opt/wg-auto
rm -f /etc/sysctl.d/90-wg-auto.conf

if (( purge_mode == 1 )); then
  printf 'Purging WireGuard configurations and client credentials...\n'
  rm -rf /etc/wireguard
  rm -rf /etc/amnezia
  rm -f /root/wireguard-client.conf
fi

printf 'Cleaning runtime lock files...\n'
rm -f /run/wg-auto-*.lock

printf 'Uninstallation completed successfully.\n'
exit 0
