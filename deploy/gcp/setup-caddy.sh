#!/usr/bin/env bash
#
# Direct-hosting path: put Caddy in front of the container with automatic
# HTTPS, instead of (or alongside) the Cloudflare Tunnel in setup.sh.
#
#   sudo bash deploy/gcp/setup-caddy.sh 34.26.115.25.sslip.io
#   sudo bash deploy/gcp/setup-caddy.sh nfl.example.com
#
# Idempotent: re-run after editing deploy/gcp/Caddyfile. Requires the GCE
# firewall to allow tcp:80,443 to this instance's tag (DEPLOY_VM.md §3b) --
# Caddy needs :80 for the ACME challenge even if nobody visits http://.
#
# This is the opposite of what setup.sh assumes ("no inbound web ports"). Both
# are legitimate; pick one and keep DEPLOY_VM.md's checklist item 4 in mind:
# with Caddy, 80/443 are *supposed* to answer from the internet, and 8600
# still must not.
set -euo pipefail

HOST="${1:-}"
if [[ -z "$HOST" ]]; then
  echo "usage: sudo bash $0 <public-hostname>" >&2
  exit 2
fi
if [[ $EUID -ne 0 ]]; then echo "run with sudo" >&2; exit 1; fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The VM's external IP, for the http://<ip> -> https://<host> redirect block.
# Read from the metadata server rather than guessed, so a re-created VM with a
# different address gets the right redirect without a code change.
IP="$(curl -fsS -H 'Metadata-Flavor: Google' \
  'http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip')"

# ----------------------------------------------------------------- 1. Caddy
# Official repo, same as the old Oracle setup. Debian's caddy package is old
# enough to matter for ACME behaviour.
if ! command -v caddy >/dev/null; then
  echo "==> Installing Caddy"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy >/dev/null
else
  echo "==> Caddy already installed ($(caddy version | cut -d' ' -f1))"
fi

# ------------------------------------------------------------- 2. Caddyfile
echo "==> Writing /etc/caddy/Caddyfile for $HOST (ip $IP)"
mkdir -p /var/log/caddy
chown caddy:caddy /var/log/caddy
sed -e "s/__HOST__/$HOST/g" -e "s/__IP__/$IP/g" "$SCRIPT_DIR/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null

# --------------------------------------------------------------- 3. Enable
systemctl enable --now caddy >/dev/null
systemctl reload caddy || systemctl restart caddy

echo
echo "=========================================================="
echo " Caddy is up. Certificate issuance happens on first request;"
echo " give it ~30s, then:"
echo "   curl -sI https://$HOST/api/health"
echo "   journalctl -u caddy -n 30      # if the cert does not appear"
echo "=========================================================="
