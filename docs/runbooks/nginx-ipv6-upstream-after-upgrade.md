# System nginx proxying to [::1]:10000 after the upgrade

Prod design node, found and fixed some days after the upgrade.

## Two nginx processes on the design node

```
root      ... nginx: master process /usr/sbin/nginx
nginx     ... nginx: worker process
dataiku   ... nginx: master process /usr/sbin/nginx -c /data/dataiku/dss_data/install-support/nginx.conf
dataiku   ... nginx: worker process
```

| | System nginx | DSS nginx |
|---|---|---|
| Runs as | root master, `nginx` worker | `dataiku` |
| Config | `/etc/nginx/nginx.conf` plus `/etc/nginx/conf.d/dataiku-reverse-proxy.snippet` | `/data/dataiku/dss_data/install-support/nginx.conf` (only readable as dataiku) |
| Listens | `0.0.0.0:443`, TLS termination | `0.0.0.0:10000` |
| Role | Front door for the ALB, proxies to DSS on 10000 | DSS's own front end |
| Error log | `/var/log/nginx/error.log` (`-rw-r----- nginx root`, read with sudo) | under `run/nginx/` |
| Service | `nginx.service` (disabled preset, drop-in `limit.conf`) | supervised by DSS |

## Symptom

Trying to read `/var/log/nginx/error.log` gave permission denied, which is what made us look. With sudo it was full of:

```
[error] ...: *586945 connect() failed (111: Connection refused) while connecting to upstream,
client: <CLIENT_IP>, server: defaultdssdomain, request: "GET / HTTP/1.1", upstream: "http://[::1]:10000/", host: "<DESIGN_NODE_IP>"
```

including user requests such as `GET /dip/api/futures/get-update?futureId=...` from the Code Studio admin page.

DSS itself was healthy:

```
$ sudo -u dataiku /data/dataiku/dss_data/bin/dss status
backend    RUNNING   pid 30998, uptime 5 days, 1:04:43
ipython    RUNNING   pid 30999, uptime 5 days, 1:04:43
nginx      RUNNING   pid 31000, uptime 5 days, 1:04:43
$ ss -tlnp | grep 10000
LISTEN 0      511          0.0.0.0:10000      0.0.0.0:*
```

## Cause

The snippet said `proxy_pass http://localhost:10000;`. On this host `localhost` resolves to `::1` first, and DSS listens on IPv4 only. Every proxied request was refused.

Proved before changing anything:

```bash
getent hosts localhost                                           # ::1  localhost
ss -tlnp | grep 10000                                            # 0.0.0.0:10000 only
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:10000/   # 200
curl -s -o /dev/null -w "%{http_code}" http://[::1]:10000/       # 000
```

Why it started after the upgrade was not established. Either the OS image changed `localhost` resolution (the node had been reprovisioned by FM onto 14.5.1, and the system nginx had last started a week after that) or DSS stopped binding IPv6.

## Fix

From the ssm-user shell (the dataiku user has no passwordless sudo: `sudo: a password is required`):

```bash
sudo sed -i 's|proxy_pass http://localhost:10000|proxy_pass http://127.0.0.1:10000|' /etc/nginx/conf.d/dataiku-reverse-proxy.snippet
sudo nginx -t && sudo systemctl reload nginx
```

Resulting snippet:

```
proxy_pass http://127.0.0.1:10000;
proxy_read_timeout 7200;
proxy_send_timeout 7200;
client_max_body_size 0;
proxy_http_version 1.1;
proxy_set_header Host $http_host;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_pass_header X-Accel-Buffering;
```

Rollback, if ever needed:

```bash
sudo sed -i 's|proxy_pass http://127.0.0.1:10000|proxy_pass http://localhost:10000|' /etc/nginx/conf.d/dataiku-reverse-proxy.snippet
sudo nginx -t && sudo systemctl reload nginx
```

## Verifying (and how we briefly thought it had made things worse)

`curl http://localhost/` and `curl http://127.0.0.1/` both returned `000` after the change, which looked like we had broken prod. They were the wrong tests: the system nginx only listens on 443, not 80. `ss -tlnp | grep nginx` shows nothing from ssm-user because process names need root, and `grep 443` also matches unrelated ports like 44321. The right test:

```bash
ss -tlnp | grep ':443 '
curl -sk -o /dev/null -w "%{http_code}" https://127.0.0.1/      # 200
sudo tail -5 /var/log/nginx/error.log
```

The last `[::1]:10000` error was logged 16 seconds before the reload signal, and nothing after. The old worker kept serving with the old config for a short while before it finished shutting down.

## Open items

* The snippet is outside DSS and outside Terraform. A node rebuild may bring `localhost` back. Add this to the post-upgrade checklist in `dss-upgrade-14.5.1.md` until the snippet is managed in the instance template.
