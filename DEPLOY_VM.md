# Deploying on one GCE VM behind a Cloudflare Tunnel

The app runs as a single always-on container on a Google Compute Engine VM. The
container holds FastAPI, the built SPA, and the live ESPN ingester. Postgres
stays at Supabase. Nothing is exposed to the internet directly: a Cloudflare
Tunnel dials out from the box, so the VM has no inbound HTTP ports at all.

> **Status of this document.** It was written against the code, not against a
> running VM. Commands marked *unverified* have not been executed anywhere.

## Why this shape

The previous deployment split the app across Cloud Run (API, scale-to-zero),
Cloud Run Jobs (a poll job on a 10-minute Scheduler cron), and Cloudflare Pages
(the SPA). It was free and it worked, but the 10-minute poll interval was the
whole cost of it: the in-process ingester polls every ~45 seconds during live
games, and a scale-to-zero container cannot hold a background loop. "Live"
scores that are ten minutes old are not live.

An always-on VM gets the 45-second poller back. That is the entire trade.

| Layer | Before | Now |
|---|---|---|
| API + SPA | Cloud Run + Cloudflare Pages, two origins | one container on one VM, same origin |
| Live ingestion | Cloud Run Job, `*/10 * * * *` | in-process, ~45s during games |
| nflverse refresh | Cloud Run Job, daily | systemd timer or `docker exec` on the VM |
| Database | Supabase Postgres | unchanged |
| Ingress | Cloud Run URL + Pages CDN | Cloudflare Tunnel → 127.0.0.1:8600 |
| Static assets | Pages CDN | Cloudflare edge in front of the tunnel |

Consequences worth naming up front:

- **One box is a single point of failure.** A `systemctl restart` is a few
  seconds of 502. Cloud Run's rolling replace was better at this. Accepted.
- **The SPA is inside the image.** A frontend-only change now requires a full
  image build and a container restart.
- **CORS is gone.** Same origin, so no `ALLOWED_ORIGINS`, no preview-domain
  regex, no `VITE_API_BASE`.

---

## 1. The database (Supabase)

Unchanged from the previous deployment, but which pooler you use has changed.

Two connection strings matter and they are **not** interchangeable:

| Use | Connection | Host / port |
|---|---|---|
| Alembic migrations, `psql`, seeding | **Session pooler** | `*.pooler.supabase.com:5432` |
| The VM | **Session pooler** | `*.pooler.supabase.com:5432` |

Use the *pooler* host in both cases, never `db.<ref>.supabase.co`. Supabase's
direct endpoint is IPv6-only and does not resolve from GCE.

The VM now uses the **session** pooler (5432), not the transaction pooler
(6543) the Cloud Run service used. Transaction-mode pooling existed to let many
short-lived, independently-scaling instances share a small connection budget.
There is exactly one long-lived process now, so session mode — which supports
DDL and server-side prepared statements — is the better fit and removes a class
of failure the app had to work around.

The `prepare_threshold=None` in
[backend/app/db/session.py](backend/app/db/session.py) is still set. On the
session pooler it is unnecessary but harmless; leave it, because migrations and
one-off scripts still run through the same code path.

Supabase's free tier pauses a project after **7 days of zero activity**, which
the always-on poller makes unreachable.

### Seeding

Seed from a workstation, **not from the VM**:

```bash
DATABASE_URL='<session pooler 5432 url>' alembic upgrade head
DATABASE_URL='<session pooler 5432 url>' python -m app.cli seed
```

The backfill needs ~2 GB of memory and downloads several hundred MB of parquet.
The VM has 2 GB total and later 1 GB. This is why `/etc/nfl-analytics.env` sets
`AUTO_SEED=0` — it defaults to *on*, and a `DATABASE_URL` pointing at an empty
database (a typo, a fresh project, a restored branch) would otherwise make the
VM attempt exactly this on boot.

---

## 2. Create the VM

```bash
gcloud compute instances create nfl-analytics \
  --zone=us-east1-b --machine-type=e2-small \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-type=pd-standard --boot-disk-size=30GB \
  --service-account=nfl-vm@<project>.iam.gserviceaccount.com \
  --scopes=https://www.googleapis.com/auth/cloud-platform \
  --metadata=enable-oslogin=TRUE \
  --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring
```

### Three flags you cannot fix later

**`--boot-disk-type=pd-standard`.** gcloud defaults to `pd-balanced`, which is
**not** in the always-free tier — the free 30 GB-months covers *standard*
persistent disk only. Boot disk type cannot be changed on an existing instance:
correcting it means snapshot, delete, recreate. Get it right the first time.

**`--boot-disk-size=30GB`.** 30 GB is the free allowance, and the image is not
small (polars + pyarrow). Growing a disk later is possible; shrinking is not.

**`--zone=us-east1-b`.** Only three regions have an always-free e2-micro:
`us-east1`, `us-west1`, `us-central1`. Anywhere else is billed from day one.
Put the Artifact Registry repo in the same region as the VM so image pulls do
not cross regions.

`e2-small` (2 vCPU burst / 2 GB) is for the 90-day trial-credit period.
Downsizing to the always-free `e2-micro` is §9.

---

## 3. Firewall: create nothing

The tunnel is **outbound-only**. `cloudflared` opens connections to
Cloudflare's edge; nothing ever connects *to* the VM on 80 or 443. So:

**Do not create any ingress rule for 80 or 443.** Not "restricted to Cloudflare
IP ranges" — none at all. If you find yourself opening a web port, the tunnel
is misconfigured.

The only ingress rule the deployment needs is SSH from Google's IAP range, so
CI can reach the box without the VM having a public SSH surface:

```bash
gcloud compute firewall-rules create nfl-allow-iap-ssh \
  --direction=INGRESS --action=allow \
  --rules=tcp:22 --source-ranges=35.235.240.0/20 \
  --target-tags=nfl-analytics
```

`35.235.240.0/20` is the fixed IAP TCP-forwarding range. Connect with
`gcloud compute ssh nfl-analytics --tunnel-through-iap`.

> This is the **opposite** of what the old Oracle Cloud setup script did. That
> script ended by punching TCP 80 and 443 through the instance's local iptables
> (`deploy/oracle/setup.sh`, the "Opening ports 80/443" block), because Oracle's
> images ship a default-REJECT chain and Caddy needed to terminate TLS on the
> box. There is no Caddy here and no TLS on the box; that block must not be
> carried over. Cloudflare terminates TLS at the edge.

---

## 4. Service accounts and IAM

Two identities, doing different things.

**The VM's own service account** (`nfl-vm@<project>`) only needs to pull
images:

| Role | Why |
|---|---|
| `roles/artifactregistry.reader` | `docker pull` from Artifact Registry |
| `roles/logging.logWriter` | optional, if you install the Ops Agent |

Do **not** give it `roles/editor`, which is what `--scopes=cloud-platform`
combined with the *default* compute service account would effectively hand it.
Create a dedicated account.

**The CI service account** (the one already federated in
`.github/workflows/deploy.yml`) needs to push an image and then SSH in:

| Role | Why |
|---|---|
| `roles/artifactregistry.writer` | `docker push` |
| `roles/iap.tunnelResourceAccessor` | open the IAP tunnel to :22 |
| `roles/compute.osAdminLogin` | SSH **and** passwordless sudo on the box |
| `roles/iam.serviceAccountUser` on `nfl-vm@` | required to SSH to an instance that runs as a service account |

`roles/compute.osAdminLogin` (rather than `osLogin`) is what makes
`sudo /usr/local/bin/nfl-deploy $IMAGE` work: OS Login puts admin principals in
the `google-sudoers` group, which already has passwordless sudo. If you prefer
to grant only `roles/compute.osLogin`, add a sudoers drop-in on the box
scoped to just this command — but note the OS Login username for a service
account is `sa_<numeric-unique-id>`, which you have to read off the box after
the first login. *Unverified.*

---

## 5. Provision the box

SSH in and run the setup script from a checkout:

```bash
gcloud compute ssh nfl-analytics --zone=us-east1-b --tunnel-through-iap

sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/paamujul/nfl_analytics.git
sudo bash nfl_analytics/deploy/gcp/setup.sh \
  us-east1-docker.pkg.dev/<project>/nfl-analytics/nfl-analytics:latest
```

[`deploy/gcp/setup.sh`](deploy/gcp/setup.sh) is idempotent — re-run it after any
change to the units. It installs Docker CE, creates the swapfile, creates the
data directory, writes the config files, installs `nfl-deploy` and both systemd
units, and configures Artifact Registry credentials for root.

Then fill in the database URL and start the app:

```bash
sudo nano /etc/nfl-analytics.env      # set DATABASE_URL
sudo systemctl start nfl-analytics
curl -s localhost:8600/api/status | head -c 400
```

### What the script sets up, and why each piece matters

**A 2 GB swapfile with `vm.swappiness=10`.** This is not padding. It converts a
memory spike into a slow request instead of an OOM kill, which is the
difference between a bad thirty seconds and a hard restart — and it is what
makes the eventual 1 GB e2-micro survivable at all. `swappiness=10` keeps the
kernel from using swap under ordinary load; it is an emergency cushion, not a
memory tier.

**`/var/lib/nfl-analytics`, owned by uid 10001.** The container runs as
`nflapp`, created in [backend/Dockerfile](backend/Dockerfile) with
`--uid 10001`. Bind-mounting a host directory over `/app/storage` masks the
image's own ownership of that path, so if the host directory is not writable by
10001, `app/config.py`'s import-time `STORAGE_DIR.mkdir()` raises
`PermissionError` and *every* entrypoint — the API, `app.cli`, alembic — dies
before printing anything useful.

There is no `nflapp` user on the VM and there does not need to be. The kernel
compares numeric uids across the container boundary; the name only affects what
`ls -l` prints on the host.

**`/etc/nfl-analytics.env`**, root-owned `0600`, passed to the container with
`--env-file`. Exactly these five variables:

```
DATABASE_URL=<Supabase session pooler, port 5432>
STORAGE_DIR=/app/storage
STATIC_DIR=/app/static
AUTO_SEED=0
DB_POOL_SIZE=5
```

Two absences are deliberate:

- **No `DISABLE_INGEST`.** Turning the in-process poller back on is the entire
  point of this migration. Setting it would restore the 10-minute lag.
- **No `ALLOWED_ORIGINS`.** The SPA ships inside the image and FastAPI serves
  it from this same origin. There is no cross-origin request left to allow.

**`/etc/nfl-analytics.image`**, holding one line, `IMAGE=<tag>`. systemd reads
it as an `EnvironmentFile` and expands `${IMAGE}` in `ExecStart`. Keeping the
image pointer out of the secrets file means the deploy path never touches
`DATABASE_URL`, and a rollback is a one-line edit.

---

## 6. Cloudflare Tunnel

A **named tunnel with a locally-managed config** — not a dashboard-managed
token. The ingress rules are then reviewable in git and reproducible if the box
has to be rebuilt.

Install `cloudflared` from Cloudflare's apt repo, then:

```bash
cloudflared tunnel login                       # opens a browser; pick the zone
cloudflared tunnel create nfl-analytics        # prints a UUID, writes ~/.cloudflared/<UUID>.json
sudo mkdir -p /etc/cloudflared
sudo mv ~/.cloudflared/<UUID>.json /etc/cloudflared/
sudo chmod 600 /etc/cloudflared/<UUID>.json
cloudflared tunnel route dns nfl-analytics <your-domain>   # creates the proxied CNAME
```

Copy [`deploy/gcp/cloudflared-config.yml`](deploy/gcp/cloudflared-config.yml)
to `/etc/cloudflared/config.yml` (setup.sh leaves it there as
`config.yml.example`) and substitute the UUID and hostname:

```yaml
tunnel: <UUID>
credentials-file: /etc/cloudflared/<UUID>.json
ingress:
  - hostname: <your-domain>
    service: http://127.0.0.1:8600
    originRequest:
      connectTimeout: 10s
  - service: http_status:404
```

One ingress rule is all it takes. The SPA and the API share an origin, so there
is no path matching to do; the trailing `http_status:404` is the catch-all
`cloudflared` requires as the last rule.

Then:

```bash
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared
```

> **Do not run `cloudflared service install`.** It generates its own unit at
> `/etc/systemd/system/cloudflared.service` — the same path — and would
> overwrite the one in
> [`deploy/gcp/cloudflared.service`](deploy/gcp/cloudflared.service) with a
> token-based one whose routing lives only in the Cloudflare dashboard. That is
> the setup this section is specifically avoiding.

---

## 7. The Cloudflare Cache Rule (required)

**The free plan does not cache JSON.** Cloudflare's default cache behaviour
keys off file extension, so it caches `.js`, `.css`, images and fonts — the SPA
assets, which is why static serving is fine out of the box — and treats
`/api/...` as uncacheable regardless of what `Cache-Control` the app sends.

That means any cache headers the API emits do **nothing at the edge** until you
add a Cache Rule. Without one, every dashboard load hits the single VM and the
single Supabase free-tier database.

Create one under **Rules → Caching → Cache Rules**:

- **When incoming requests match:**
  ```
  (starts_with(http.request.uri.path, "/api/")
   and not starts_with(http.request.uri.path, "/api/live")
   and not starts_with(http.request.uri.path, "/api/health")
   and not starts_with(http.request.uri.path, "/api/status"))
  ```
- **Then:** Cache eligibility → **Eligible for cache**
- **Edge TTL:** *Use cache-control header if present, otherwise 60s* (or a
  fixed short TTL if the app is not yet sending headers)
- **Browser TTL:** respect origin

The three exclusions are the endpoints where a stale answer is a bug, not an
optimisation:

| Endpoint | Why it must not be cached |
|---|---|
| `/api/live` | in-progress games; the whole feature is freshness |
| `/api/health` | 503-when-stale is the monitoring signal; a cached 200 hides an outage |
| `/api/status` | `nfl-deploy` polls it as its deploy gate, and a cached response would make a broken image look healthy |

Everything else — team dashboards, player pages, comparisons, defensive
profiles — is derived from data that only changes when the ingester writes, and
is safe to cache for at least a minute.

After adding the rule, confirm at the edge:

```bash
curl -sI https://<your-domain>/api/teams   | grep -i cf-cache-status   # expect MISS then HIT
curl -sI https://<your-domain>/api/live    | grep -i cf-cache-status   # expect BYPASS/DYNAMIC
```

---

## 8. Deploys and rollback

CI builds and pushes the image, runs migrations, then SSHes in over IAP and
runs one command:

```bash
sudo /usr/local/bin/nfl-deploy "$IMAGE"
```

[`deploy/gcp/nfl-deploy`](deploy/gcp/nfl-deploy) does the whole rollout:

1. record the tag currently in `/etc/nfl-analytics.image`
2. `docker pull` the new tag
3. write the new tag, `systemctl restart nfl-analytics`
4. poll `http://127.0.0.1:8600/api/status` for up to 60 seconds
5. **on failure: restore the previous tag, restart, and exit non-zero** — a
   real rollback, not just a red build
6. on success: `docker image prune -af --filter "until=168h"`

The prune matters on a 30 GB disk. The image carries polars and pyarrow;
without it, a few dozen deploys fill the boot volume and the next `docker pull`
fails in a way that looks like a network problem.

**The health check is `/api/status`, not `/api/health`.** `/api/health` returns
503 once the last successful sync is more than six hours old
([backend/app/api/routes.py](backend/app/api/routes.py),
`HEALTH_MAX_SYNC_AGE`). That is correct monitoring behaviour and the wrong
deploy gate: after any quiet stretch — an offseason week, a Tuesday — a
perfectly good deploy would fail its check and get rolled back.

### Shutdown deadlines must nest: 30 < 45 < 60

`LiveIngester.stop()`
([backend/app/data/ingest.py](backend/app/data/ingest.py)) waits up to **30s**
for the in-flight poll cycle to finish rather than cancelling a coroutine in the
middle of an open transaction. For that code to ever run:

| Layer | Setting | Where |
|---|---|---|
| app | 30s ingester drain | `ingest.py` |
| docker | `--stop-timeout 45` and `docker stop -t 45` | `nfl-analytics.service` |
| systemd | `TimeoutStopSec=60` | `nfl-analytics.service` |

Docker's **default** stop timeout is 10 seconds. Omit any of the above and
Docker SIGKILLs the container at 10s on *every single deploy*, and the graceful
shutdown path never executes — silently, with no error anywhere.

### Manual rollback

```bash
sudo nfl-deploy <previous-image-tag>
```

The previous week of images are still on disk, so this does not even need a
pull.

---

## 9. Cost, and the day-91 downsize

During the trial, GCP credits cover the `e2-small`. After they expire, only
these are always-free:

| Resource | Always-free allowance |
|---|---|
| VM | **one** `e2-micro` (2 vCPU burst, **1 GB**) in us-east1 / us-west1 / us-central1 |
| Boot disk | 30 GB-months **pd-standard** (not pd-balanced) |
| Egress | 1 GB/month from North America |

**Set the GCP budget alert to $5, not $1.** An in-use external IPv4 address is
billed at roughly $0.005/hour — about **$3/month** — and is *not* covered by the
always-free tier. A $1 alert would fire every single month on a deployment
that is behaving perfectly, and an alert that always fires is an alert nobody
reads.

(You could drop the external IP entirely: the tunnel is outbound-only and IAP
handles SSH. Egress would then need Cloud NAT, which costs more than the IP.
Keep the IP.)

### Downsizing to e2-micro

```bash
sudo systemctl stop nfl-analytics cloudflared
gcloud compute instances stop nfl-analytics --zone=us-east1-b
gcloud compute instances set-machine-type nfl-analytics \
  --zone=us-east1-b --machine-type=e2-micro
gcloud compute instances start nfl-analytics --zone=us-east1-b
```

Then **edit `/etc/systemd/system/nfl-analytics.service` to change
`--memory 1200m` to `--memory 700m`** (and update
`deploy/gcp/nfl-analytics.service` in git to match), `systemctl daemon-reload`,
and restart. On a 1 GB box, a 1200m container limit means the kernel OOM killer
picks the target instead of Docker, and it will not necessarily pick the
container.

The 2 GB swapfile is what makes 700m workable. Verify after the switch that a
cold dashboard load still completes, and watch `journalctl -k | grep -i oom`
for the first week.

---

## 10. Monitoring

Point UptimeRobot at the public hostname:

- `GET /` — liveness, touches no database.
- `GET /api/health` — returns **503** once the last successful sync is more
  than six hours old. This is the one worth alerting on: it catches a wedged
  ingester, which a plain 200 check never would.

`/api/health` is excluded from the Cache Rule (§7) precisely so this signal
reaches the monitor rather than a cached 200.

On the box:

```bash
journalctl -u nfl-analytics -f          # app logs (--log-driver journald)
journalctl -u cloudflared -n 50         # tunnel state
systemctl status nfl-analytics
docker stats --no-stream                # memory headroom
free -h                                 # swap in use -- steady growth is a leak
```

`swap used` climbing steadily rather than sitting near zero means something is
leaking; that is the number to watch after the e2-micro downsize.

---

## 11. Cutover from Cloud Run

The two deployments can run side by side against the same database, which makes
this reversible until the DNS record moves.

1. Build and push an image from the VM branch; verify the SPA is inside it
   (`docker run --rm <image> ls /app/static`).
2. Provision the VM (§2–§5) and start it. Check `/api/status` over the tunnel
   hostname, on a temporary subdomain if the real one is still on Pages.
3. Watch it for one full game window. The point of the migration is the
   45-second poller — confirm `/api/live` actually updates on that cadence and
   that memory stays under the container limit while it does.
4. Move DNS: point the production hostname at the tunnel (the proxied CNAME
   `cloudflared tunnel route dns` created).
5. Add the Cache Rule (§7) and confirm `cf-cache-status`.
6. **Leave Cloud Run deployed and the Scheduler jobs enabled for ~48 hours.**
   The rollback is a DNS change back to Pages.
7. After 48 clean hours: pause the Cloud Scheduler jobs, delete the Cloud Run
   service and jobs, and delete the Pages project. Leave Artifact Registry and
   Supabase alone — the VM uses both.

**Rolling back after step 4** is: point DNS back at Pages, re-enable the
Scheduler jobs. Do not delete anything in step 7 until you are sure, because
recreating the Scheduler jobs and Pages build config is the slow part.

---

## 12. Season rollover

The ESPN poller picks up phase changes on its own. The nflverse refresh is the
only thing that names a season. It is no longer a Cloud Run Job; run it on the
VM against the running container:

```bash
sudo docker exec nfl-analytics python -m app.cli refresh-nflverse 2027
```

Scheduling it as a systemd timer is the obvious follow-up. Note the memory: the
refresh loads season parquet and wanted 2 GB as a Cloud Run Job, which is more
than an e2-micro has. Run it by hand from a workstation against the session
pooler, or accept that it leans on swap.

---

## Verification checklist

Nothing below has been run. It is the list to work through on the real box.

1. `sudo bash deploy/gcp/setup.sh <image>` completes, and re-running it changes
   nothing.
2. `free -h` shows 2.0Gi swap; `cat /proc/sys/vm/swappiness` is 10.
3. `stat -c '%u:%g' /var/lib/nfl-analytics` is `10001:10001`.
4. `sudo ss -lntp | grep 8600` shows `127.0.0.1:8600` only — **not** `0.0.0.0`
   or `*`. From another host, `nc -vz <vm-public-ip> 8600` must fail.
5. `curl -s localhost:8600/api/status` returns 200 with a recent
   `last_successful_sync`.
6. The public hostname serves the SPA and its `/api` calls with no CORS errors
   and no `VITE_API_BASE` involved.
7. `sudo nfl-deploy <good-image>` succeeds; `sudo nfl-deploy <deliberately
   broken image>` fails, restores the previous tag, and leaves the site up.
8. `sudo systemctl stop nfl-analytics` takes visibly longer than 10 seconds
   during a live game, and the journal shows the ingester's shutdown message —
   proof the 30/45/60 nesting works.
9. `curl -sI https://<domain>/api/teams` shows `cf-cache-status: HIT` on the
   second request; `/api/live` does not.
10. Break `DATABASE_URL` on purpose: `/api/health` goes 503 and UptimeRobot
    alerts.
11. Reboot the VM. Both units come back on their own and the site is up with no
    intervention.
