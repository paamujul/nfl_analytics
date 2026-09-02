# Deploying free & 24/7 on an Oracle Cloud VM

This is the **free** deployment path: one Always Free ARM VM running the FastAPI
backend (with its live ingester) under systemd, behind Caddy for automatic
HTTPS, serving the React frontend from the same box.

Why a VM rather than Vercel/Render/Pages: this app needs a process that stays
alive between requests (the ESPN poller) and a disk that survives restarts (the
SQLite database). Free serverless tiers give you neither, and the 512 MB free
container tiers can't hold the nflverse seed in memory. Oracle's Always Free
ARM instance has 24 GB of RAM and a real disk, indefinitely.

For the paid one-click alternative (~$5/mo, ~10 minutes), see [DEPLOY.md](DEPLOY.md).

**Everything here needs your own accounts** — create the Oracle account and DNS
records yourself; the setup script does the rest on the box.

---

## 1. Create the VM

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com). A card is required for
   identity verification; Always Free resources don't charge it. **Pick your home
   region carefully** — Always Free resources only exist in the home region and it
   can't be changed later.
2. **Compute → Instances → Create instance.**
3. **Image:** Ubuntu 24.04 (Canonical Ubuntu).
4. **Shape:** *Ampere → VM.Standard.A1.Flex*, **2 OCPU / 12 GB** (the free
   allowance is 4 OCPU / 24 GB total — 2/12 leaves room for a second box).

   > **Do not pick the AMD `VM.Standard.E2.1.Micro`.** It has 1 GB of RAM and the
   > first-boot nflverse seed will be OOM-killed part way through.

5. **Boot volume:** leave it at the default **50 GB**.

   > The cost estimator on this screen will quote about **$2.00/month** for the
   > boot volume. Ignore it — it prices at list rate and says so in the fine
   > print (*"does not reflect any tier unit pricing"*). Always Free includes
   > 200 GB of block storage, which boot volumes draw from, so a 50 GB disk is
   > $0. Just don't size it up, and check that the shape shows the **Always Free
   > eligible** badge — that badge, not the estimate, decides what you pay.
   > (50 GB is ample: Ubuntu + venv + the parquet cache and database stay under
   > 10 GB.)

6. **SSH keys:** upload your public key (`~/.ssh/id_ed25519.pub`) or let Oracle
   generate one and save the private key.
7. Keep the default VCN with a public IPv4 address. Create the instance and note
   the **public IP**.

### “Out of host capacity for shape VM.Standard.A1.Flex”

Expected, not a mistake on your part — free ARM capacity is heavily contended and
frees up constantly, so it's about asking at the right moment. In rough order of
effort:

1. **Don't pin a fault domain**, and try each **availability domain** (AD-1,
   AD-2, AD-3). Many regions only have one, so this may not apply.
2. **Ask for less.** A **1 OCPU / 6 GB** A1 fits into fragmented capacity far more
   often than 2/12, and 6 GB is still plenty for this app.
3. **Retry on a loop** rather than clicking. The repo ships
   [`deploy/oracle/retry-launch.sh`](deploy/oracle/retry-launch.sh), which rotates
   through every AD and retries until one succeeds:

   ```bash
   pip install oci-cli && oci setup config
   export COMPARTMENT_ID=... SUBNET_ID=... IMAGE_ID=...
   OCPUS=1 MEM_GB=6 bash deploy/oracle/retry-launch.sh
   ```

   Leave it running; successful grabs often take minutes to hours.
4. **Upgrade to Pay As You Go.** This is the reliable fix — PAYG accounts aren't
   competing in the free-tier capacity queue. Always Free resources *stay* free
   after upgrading, so an A1 within 4 OCPU / 24 GB still costs nothing; the risk
   is that anything you create *beyond* the free allowances now bills for real.
5. **Give up on ARM** and use [Plan B](#plan-b-the-1-gb-amd-micro-with-a-pre-seeded-database)
   below — the always-available x86 micro shape, which works fine if you seed the
   database from your laptop instead of on the box.

Some new accounts also sit in a signup review queue for a day or two, which can
look like capacity errors.

## 2. Open ports 80 and 443 — **both** places

This is the single most common way to end up with a box that looks fine and
answers nothing. Traffic has to get through two independent firewalls:

**a) The VCN security list (Oracle console):** Instance → *Virtual cloud network*
→ *Security lists* → default list → **Add ingress rules**:

| Source CIDR | IP protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

**b) The instance's own iptables rules** — Oracle's Ubuntu images REJECT
everything except SSH by default. The setup script in step 4 handles this, so
you don't need to do it by hand.

Port 8600 stays closed everywhere: uvicorn binds to `127.0.0.1` and only Caddy
talks to it.

## 3. Point a domain at it

Create an **A record** for your domain → the instance's public IP.

**No domain?** Use [sslip.io](https://sslip.io), which resolves any
`<dashed-ip>.sslip.io` to that IP and works with Let's Encrypt. For `198.51.100.7`
that's `198-51-100-7.sslip.io` — usable as your domain everywhere below.

## 4. Run the setup script

SSH in (`ssh ubuntu@<public-ip>`) and:

```bash
git clone https://github.com/paamujul/nfl_analytics.git
sudo bash nfl_analytics/deploy/oracle/setup.sh nfl.example.com
```

It's idempotent — re-run it any time to deploy the latest `main`. What it does:

- installs Python, Node, Caddy, and `unattended-upgrades`
- clones the repo to `/opt/nfl-analytics` and builds the venv
- builds the frontend to `/var/www/nfl-analytics`
- creates the `nflapp` service user and `/var/lib/nfl-analytics` for the database
- installs and enables the [systemd unit](deploy/oracle/nfl-analytics.service)
- writes the [Caddyfile](deploy/oracle/Caddyfile) with your domain and reloads Caddy
- opens 80/443 in the instance firewall and persists the rules

Keeping the frontend on Vercel instead? Pass `--api-only`, use an `api.` subdomain,
then set `ALLOWED_ORIGINS=https://your-app.vercel.app` in `/etc/nfl-analytics.env`
and `VITE_API_BASE=https://api.example.com` in Vercel.

## 5. Watch the first boot seed itself

The database starts empty and seeds in a background thread: the live season from
ESPN first (fast), then the full 2025 nflverse season (a few minutes, ~50k plays,
several hundred MB of parquet downloads).

```bash
journalctl -u nfl-analytics -f
```

```bash
curl -s https://nfl.example.com/api/status | python3 -m json.tool | head -30
```

Seeding is done when a `seed / startup` entry reads `seeding complete`. The site
is usable before that finishes — earlier phases appear as they land.

## 6. Verify it really survives 24/7

```bash
sudo reboot
```

Then, once it's back (~30 s), confirm both services came back on their own and
the data is still there:

```bash
systemctl is-enabled nfl-analytics caddy && systemctl is-active nfl-analytics caddy
```

```bash
curl -s https://nfl.example.com/api/status | head -c 200
```

`games_in_db` should be non-zero immediately — proving the database survived the
reboot on the persistent disk rather than re-seeding.

---

## Plan B: the 1 GB AMD micro with a pre-seeded database

If A1 capacity never materialises, the always-available **VM.Standard.E2.1.Micro**
(1 OCPU / 1 GB, x86, 2 free per tenancy) can run this app after all. The only
thing that needs real memory is the *initial nflverse seed* — the live ESPN
poller is lightweight. So seed on your laptop and ship the database up: it's
only ~34 MB.

1. Create the instance exactly as above, but pick **VM.Standard.E2.1.Micro**.
2. **Add swap before anything else** — 1 GB of RAM is tight for `pip install`
   and the frontend build:

   ```bash
   sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

3. Run the setup script as usual, then **turn seeding off** in
   `/etc/nfl-analytics.env` so the box never attempts the heavy backfill:

   ```
   AUTO_SEED=0
   NFLVERSE_NIGHTLY=0
   ```

4. Ship your local database up. Use SQLite's `.backup` rather than copying the
   file — it produces a consistent snapshot even while the local app is running
   (a plain `cp` can miss data still sitting in the WAL):

   ```bash
   sqlite3 backend/storage/nfl.db ".backup /tmp/nfl-seed.db"
   scp /tmp/nfl-seed.db ubuntu@<public-ip>:/tmp/
   ```

   ```bash
   sudo systemctl stop nfl-analytics
   sudo rm -f /var/lib/nfl-analytics/nfl.db-wal /var/lib/nfl-analytics/nfl.db-shm
   sudo install -o nflapp -g nflapp -m 644 /tmp/nfl-seed.db /var/lib/nfl-analytics/nfl.db
   sudo systemctl start nfl-analytics
   ```

5. Verify: `curl -s https://your-domain/api/status` should report the full
   `games_in_db` count immediately.

**The trade-off:** live scores and box scores still update on their own, but the
deep nflverse data (EPA, participation, snap counts) no longer refreshes on the
box. Re-run `python -m app.cli backfill-nflverse 2026` locally whenever you want
it current, and repeat step 4. If the frontend build struggles for memory, run
setup with `--api-only` and host the SPA on Vercel instead.

---

## Operating it

**Deploy a new version** — re-run the setup script; it fetches `main`, rebuilds,
and restarts:

```bash
sudo bash /opt/nfl-analytics/deploy/oracle/setup.sh nfl.example.com
```

**Logs** (journald rotates them; no logrotate config needed):

```bash
journalctl -u nfl-analytics -n 100 --no-pager
```

**Restart / stop the ingester:**

```bash
sudo systemctl restart nfl-analytics
```

**Back up the database** — SQLite's `.backup` is safe on a live database. Add a
weekly cron:

```bash
sudo crontab -l 2>/dev/null | { cat; echo '0 5 * * 1 sqlite3 /var/lib/nfl-analytics/nfl.db ".backup /var/lib/nfl-analytics/nfl-backup.db"'; } | sudo crontab -
```

**Season rollover (Sept 2026):** nothing to do — `NFLVERSE_NIGHTLY=1` refreshes
nflverse daily once regular-season data starts publishing, and the ESPN ingester
picks up the phase change on its own.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Domain times out, SSH works | Missing VCN ingress rule (step 2a), or the instance iptables rules weren't saved. Check `sudo iptables -L INPUT -n --line-numbers` |
| Caddy can't get a certificate | DNS not propagated yet (`dig +short nfl.example.com`), or port 80 blocked — Let's Encrypt validates over HTTP first |
| `502 Bad Gateway` from Caddy | Backend down: `systemctl status nfl-analytics`, `journalctl -u nfl-analytics -n 50` |
| Seed dies part way, service restarts | Out of memory — you're on the 1 GB micro shape. Rebuild on Ampere A1, or add swap: `sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile` |
| `/api/status` shows ESPN errors | ESPN rejects custom User-Agent strings (the client deliberately uses httpx's default). Transient 403/5xx are retried on the next poll |
| Site loads, deep links 404 | Caddy's `try_files` block missing — re-run setup, or check `/etc/caddy/Caddyfile` |
