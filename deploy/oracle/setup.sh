#!/usr/bin/env bash
# Provision an Oracle Cloud (or any Ubuntu 22.04/24.04) box to run NFL Analytics
# 24/7 behind Caddy with automatic HTTPS. Safe to re-run: it updates in place.
#
#   sudo bash setup.sh nfl.example.com            # API + SPA on one box
#   sudo bash setup.sh api.example.com --api-only # API only (frontend elsewhere)
#
set -euo pipefail

DOMAIN="${1:-}"
MODE="${2:-full}"
REPO="${REPO_URL:-https://github.com/paamujul/nfl_analytics.git}"
APP_DIR=/opt/nfl-analytics
DATA_DIR=/var/lib/nfl-analytics
WEB_DIR=/var/www/nfl-analytics
ENV_FILE=/etc/nfl-analytics.env
SVC_USER=nflapp

if [[ -z "$DOMAIN" ]]; then
  echo "usage: sudo bash setup.sh <domain> [--api-only]" >&2
  echo "  no domain? use <dashed-public-ip>.sslip.io, e.g. 198-51-100-7.sslip.io" >&2
  exit 1
fi
if [[ $EUID -ne 0 ]]; then echo "run with sudo" >&2; exit 1; fi

echo "==> Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-dev \
  build-essential debian-keyring debian-archive-keyring apt-transport-https \
  iptables-persistent unattended-upgrades

# --- Caddy (official repo) -------------------------------------------------
if ! command -v caddy >/dev/null; then
  echo "==> Installing Caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -qq && apt-get install -y -qq caddy
fi

# --- Node (only needed to build the SPA on this box) -----------------------
if [[ "$MODE" != "--api-only" ]] && ! command -v node >/dev/null; then
  echo "==> Installing Node.js 22"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -qq nodejs
fi

# --- service account + directories ----------------------------------------
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" /var/log/caddy
chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR"

# --- code ------------------------------------------------------------------
if [[ -d "$APP_DIR/.git" ]]; then
  echo "==> Updating existing checkout"
  git -C "$APP_DIR" fetch --quiet origin && git -C "$APP_DIR" reset --hard --quiet origin/main
else
  echo "==> Cloning $REPO"
  git clone --quiet "$REPO" "$APP_DIR"
fi

echo "==> Python environment (this pulls polars/pyarrow wheels; give it a minute)"
python3 -m venv "$APP_DIR/backend/.venv"
"$APP_DIR/backend/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/backend/.venv/bin/pip" install --quiet -r "$APP_DIR/backend/requirements.txt"
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR"

# --- frontend --------------------------------------------------------------
if [[ "$MODE" != "--api-only" ]]; then
  echo "==> Building frontend"
  # Same-origin: Caddy proxies /api on this domain, so no VITE_API_BASE needed.
  ( cd "$APP_DIR/frontend" && npm ci --silent && npm run build --silent )
  mkdir -p "$WEB_DIR"
  rm -rf "${WEB_DIR:?}/"*
  cp -r "$APP_DIR/frontend/dist/." "$WEB_DIR/"
  chown -R caddy:caddy "$WEB_DIR"
fi

# --- config ----------------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
  echo "==> Writing $ENV_FILE"
  install -m 640 -o root -g "$SVC_USER" "$APP_DIR/deploy/oracle/nfl-analytics.env.example" "$ENV_FILE"
fi

echo "==> Installing systemd unit"
install -m 644 "$APP_DIR/deploy/oracle/nfl-analytics.service" /etc/systemd/system/nfl-analytics.service
systemctl daemon-reload
systemctl enable --now nfl-analytics

echo "==> Configuring Caddy for $DOMAIN"
if [[ "$MODE" == "--api-only" ]]; then
  cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8600
}
EOF
else
  sed "s/nfl\.example\.com/$DOMAIN/" "$APP_DIR/deploy/oracle/Caddyfile" > /etc/caddy/Caddyfile
fi
chown root:caddy /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

# --- firewall --------------------------------------------------------------
# Oracle's Ubuntu images ship iptables rules that REJECT everything except SSH.
# Opening the ports in the OCI console alone is NOT enough — the VM drops them.
echo "==> Opening ports 80/443 on the instance firewall"
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
else
  for port in 80 443; do
    iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
      || iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
  done
  netfilter-persistent save >/dev/null
fi

echo
echo "=========================================================="
echo " Done. https://$DOMAIN"
echo
echo " The database seeds itself on first boot (2025 nflverse +"
echo " the live season). It takes a few minutes — watch it with:"
echo "   curl -s https://$DOMAIN/api/status | head -c 400"
echo "   journalctl -u nfl-analytics -f"
echo
echo " Remember: add ingress rules for TCP 80 and 443 to this"
echo " instance's VCN security list in the OCI console, or the"
echo " traffic never reaches the box."
echo "=========================================================="
