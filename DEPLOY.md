# Putting the lucky draw on the server

The website runs in Docker on the server. The Tally PC in the shop runs a small **connector** that reads Tally and
sends the bills to the website over the internet (only outgoing connections from the shop, nothing to open on the shop's router).

```
Tally PC  --connector.bat-->  https://gauri.dummysite.cloud/coupon-draw/  (website + database on the server)
```

## 1. Get the code on the server

```bash
git clone https://github.com/Sahil-o-0/-gauri-ganpati-coupon-draw.git gauri-draw
cd gauri-draw
bash deploy/install.sh
```

`install.sh` installs Docker if missing, builds the app, starts it on `127.0.0.1:8080`, and shows what is using ports 80 and 443.

## 2. Publish it at /coupon-draw/

The app is served under a sub-path, so the web server in front must strip `/coupon-draw`:

| Web server on ports 80/443 | What to add |
| --- | --- |
| nginx | `deploy/nginx-snippet.conf` inside the `server { }` of gauri.dummysite.cloud, then `nginx -t && systemctl reload nginx` |
| Caddy | `deploy/Caddyfile.snippet` inside the `gauri.dummysite.cloud { }` block, then `caddy reload` |
| nothing | install Caddy and use `deploy/Caddyfile.standalone` (it fetches the HTTPS certificate by itself) |

Open `https://gauri.dummysite.cloud/coupon-draw/` (keep the last slash). The first screen creates the admin login.

If the site is served from another address or path, set `PUBLIC_URL` and `BASE_PATH` in a `.env` file next to `docker-compose.yml`
(for example `BASE_PATH=` and `PUBLIC_URL=https://example.com` for a site at the root) and run `docker compose up -d`.

## 3. Connect the Tally PC

Log in as admin, open **Tally setup**, follow the three steps (turn on Tally's server, download and start the connector, check Tally).

## Updates, backups, logs

- Update: `git pull && docker compose up -d --build`
- Logs: `docker compose logs -f app`
- Data: the `data/` folder next to `docker-compose.yml` (`data/db` live database, `data/backup` hourly copy that replaces the previous one).
  Copy `data/backup/luckydraw-backup.db` off the server now and then.
- Restore: stop (`docker compose down`), copy the backup over `data/db/luckydraw.db`, delete `data/db/luckydraw.db-wal` and `-shm`, start again.

## Security notes

- Always use HTTPS. Logins and the connector key travel over the internet.
- The app listens only on 127.0.0.1; only the web server in front is public.
- Keep `connector.ini` private. **Make a new key** on the Tally setup page if it ever leaks.
- Use a strong admin password and a key-based SSH login on the server.
