#!/usr/bin/env bash
#
# Provision a Debian 12 GCE VM to run NFL Analytics 24/7 as a Docker container
# under systemd, reachable only through a Cloudflare Tunnel.
#
#   sudo bash deploy/gcp/setup.sh us-east1-docker.pkg.dev/PROJECT/REPO/nfl-analytics:TAG
#   sudo bash deploy/gcp/setup.sh            # re-run to update units, keep the image
#
# Idempotent: safe to re-run after every change to the units or this script.
# It does NOT configure the Cloudflare Tunnel -- that needs an interactive
# browser login. See DEPLOY_VM.md.
#
# The VM this expects (the disk flags are NOT changeable afterwards -- see
# DEPLOY_VM.md; pd-balanced, which gcloud defaults to, is not always-free):
#
#   gcloud compute instances create nfl-analytics \
#     --zone=us-east1-b --machine-type=e2-small \
#     --image-family=debian-12 --image-project=debian-cloud \
#     --boot-disk-type=pd-standard --boot-disk-size=30GB \
#     --service-account=nfl-vm@<project>.iam.gserviceaccount.com \
#     --scopes=https://www.googleapis.com/auth/cloud-platform \
#     --metadata=enable-oslogin=TRUE \
#     --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring
#
set -euo pipefail

IMAGE_ARG="${1:-}"

DATA_DIR=/var/lib/nfl-analytics
ENV_FILE=/etc/nfl-analytics.env
IMAGE_FILE=/etc/nfl-analytics.image
APP_UID=10001
APP_GID=10001

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then echo "run with sudo" >&2; exit 1; fi

# ---------------------------------------------------------------- 1. Docker CE
# From download.docker.com rather than Debian's docker.io: the distro package
# lags and this box is the whole production environment.
if ! command -v docker >/dev/null; then
  echo "==> Installing Docker CE"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin
  systemctl enable --now docker
else
  echo "==> Docker already installed ($(docker --version))"
fi

# ------------------------------------------------------------------- 2. Swap
# 2 GB of swap turns a memory spike into a slow request instead of an OOM kill.
# This is not optional padding: the plan is to downsize to a 1 GB e2-micro once
# the trial credits run out, and the nflverse code path allocates in bursts.
# swappiness=10 keeps the kernel from swapping under normal load -- the swap is
# an emergency cushion, not a working tier.
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  echo "==> Creating 2 GB swapfile"
  if ! fallocate -l 2G /swapfile 2>/dev/null; then
    # fallocate fails on some filesystems; dd always works, just slower.
    dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  fi
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
else
  echo "==> Swapfile already active"
fi
grep -qs '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "==> Setting vm.swappiness=10"
cat > /etc/sysctl.d/99-nfl-analytics.conf <<'EOF'
vm.swappiness=10
EOF
sysctl --quiet --system

# ------------------------------------------------------- 3. Data directory
# uid 10001 is NOT decorative. backend/Dockerfile creates `nflapp` with
# --uid 10001 and the container runs as that user. Bind-mounting a host
# directory over /app/storage masks the image's own ownership, so if the host
# directory is not writable by 10001, app/config.py's import-time
# STORAGE_DIR.mkdir() raises PermissionError and every entrypoint -- the API,
# the CLI, alembic -- dies before it prints anything useful.
#
# There is deliberately no `nflapp` user on the VM and there does not need to
# be. The kernel compares numeric ids across the container boundary; the name
# is only a convenience for `ls -l` on the host.
echo "==> Creating $DATA_DIR (owned by ${APP_UID}:${APP_GID})"
mkdir -p "$DATA_DIR/cache"
chown -R "${APP_UID}:${APP_GID}" "$DATA_DIR"
chmod 750 "$DATA_DIR"

# --------------------------------------------------------- 4. Runtime config
# Root-owned 0600: it holds the Supabase password and nothing on this box needs
# to read it except root, which is what launches the container.
if [[ ! -f "$ENV_FILE" ]]; then
  echo "==> Writing $ENV_FILE (fill in DATABASE_URL before starting)"
  cat > "$ENV_FILE" <<'EOF'
# /etc/nfl-analytics.env -- passed to the container via `docker run --env-file`.
# Root-owned, chmod 600. Not in git: it holds the database password.

# Supabase SESSION pooler, port 5432. Not the transaction pooler (6543) and not
# db.<ref>.supabase.co (IPv6-only, will not resolve from GCE).
#
# The VM is one long-lived process, so session-mode pooling is the right shape
# here -- it was the transaction pooler on Cloud Run only because many
# short-lived instances were competing for connections.
DATABASE_URL=

# Must match the container's own path and the -v bind mount in the unit file.
STORAGE_DIR=/app/storage

# Where the built SPA lives inside the image. FastAPI serves it same-origin,
# which is why there is no ALLOWED_ORIGINS here: there is no cross-origin
# request left to allow.
STATIC_DIR=/app/static

# AUTO_SEED defaults to ON. Leaving it on is genuinely dangerous on this box:
# a DATABASE_URL that points at an empty database (a typo, a fresh Supabase
# project, a restored branch) makes the VM download several hundred MB of
# parquet and run a ~2 GB backfill on a 1-2 GB machine. Seed deliberately,
# from a workstation, using the session pooler.
AUTO_SEED=0

# One always-on process against the session pooler: a handful of connections is
# plenty, and Supabase's free tier caps the total.
DB_POOL_SIZE=5

# Note what is NOT here:
#   DISABLE_INGEST  -- turning the in-process ESPN poller back ON is the entire
#                      reason for this migration. Setting it would restore the
#                      10-minute lag the VM exists to remove.
#   ALLOWED_ORIGINS -- the SPA ships inside the image and is served from this
#                      same origin. No CORS involved.
EOF
  chmod 600 "$ENV_FILE"
  chown root:root "$ENV_FILE"
else
  echo "==> $ENV_FILE already exists, leaving it alone"
  chmod 600 "$ENV_FILE"
fi

# ------------------------------------------------------- 5. Image pointer
# A separate file so `nfl-deploy` can rewrite which image is running without
# touching secrets, and so a rollback is a one-line edit. systemd reads it as
# an EnvironmentFile and expands ${IMAGE} in ExecStart.
if [[ -n "$IMAGE_ARG" ]]; then
  echo "==> Pinning image to $IMAGE_ARG"
  printf 'IMAGE=%s\n' "$IMAGE_ARG" > "$IMAGE_FILE"
elif [[ ! -f "$IMAGE_FILE" ]]; then
  echo "==> No image given and none recorded; writing a placeholder"
  printf 'IMAGE=\n' > "$IMAGE_FILE"
fi
chmod 644 "$IMAGE_FILE"

# --------------------------------------------------- 6. Deploy hook + units
echo "==> Installing /usr/local/bin/nfl-deploy"
install -m 0755 -o root -g root "$SCRIPT_DIR/nfl-deploy" /usr/local/bin/nfl-deploy

echo "==> Installing systemd units"
install -m 0644 "$SCRIPT_DIR/nfl-analytics.service" /etc/systemd/system/nfl-analytics.service
# cloudflared's unit is installed here so it is the version in git, but the
# tunnel credentials and config are created by hand (see DEPLOY_VM.md) and this
# script does not enable it -- starting it without /etc/cloudflared would just
# crash-loop.
install -d -m 0755 /etc/cloudflared
install -m 0644 "$SCRIPT_DIR/cloudflared.service" /etc/systemd/system/cloudflared.service
if [[ ! -f /etc/cloudflared/config.yml ]]; then
  install -m 0644 "$SCRIPT_DIR/cloudflared-config.yml" /etc/cloudflared/config.yml.example
fi
systemctl daemon-reload

# Re-running this script on a live box should actually apply the unit changes,
# not leave the old ExecStart running until someone notices.
if systemctl is-active --quiet nfl-analytics; then
  echo "==> nfl-analytics is running; restarting it onto the reinstalled unit"
  systemctl restart nfl-analytics
fi

# ------------------------------------------------- 7. Artifact Registry auth
# Writes /root/.docker/config.json, which is the credential store both systemd
# (docker runs as root) and `sudo nfl-deploy` actually consult. Running this as
# a normal user would configure the wrong home directory.
#
# The registry host is derived from the image tag so this works in any region.
REGISTRY="$(sed -n 's/^IMAGE=\([^/]*\)\/.*/\1/p' "$IMAGE_FILE" || true)"
if [[ -n "$REGISTRY" ]] && command -v gcloud >/dev/null; then
  echo "==> Configuring docker credentials for $REGISTRY"
  gcloud auth configure-docker "$REGISTRY" --quiet
elif ! command -v gcloud >/dev/null; then
  echo "!!  gcloud not found. GCE Debian images normally ship it; if this box"
  echo "!!  does not have it, install google-cloud-cli and then run:"
  echo "!!      sudo gcloud auth configure-docker <region>-docker.pkg.dev --quiet"
else
  echo "==> No image pinned yet; skipping docker credential setup"
fi

# ----------------------------------------------------------------- summary
systemctl enable nfl-analytics >/dev/null

echo
echo "=========================================================="
echo " Provisioned. Remaining manual steps (DEPLOY_VM.md):"
echo
echo "   1. Put the Supabase session-pooler URL in $ENV_FILE"
echo "   2. Seed the database from a workstation, not from here"
echo "   3. Set up the Cloudflare Tunnel, then:"
echo "        sudo systemctl enable --now cloudflared"
echo "   4. sudo systemctl start nfl-analytics"
echo
echo " Then check:  curl -s localhost:8600/api/status | head -c 400"
echo "              journalctl -u nfl-analytics -f"
echo "=========================================================="
