# Host Search, AI Reviewer, and MCP

This is the server deployment behind the hosted tools in the main README. It
does not run the AI Scientist or accept arbitrary experiment code. The original
Search and Reviewer implementations and review prompt remain in their component
directories. The gateway only adds a public HTTP/MCP interface, a persistent
review queue, rate limits, and seven-day deletion.

The service files assume Ubuntu 24.04 and a checkout at
`/opt/theappliedscientist`. Use a host with at least 8 GB RAM and 25 GB free.
Point a DNS name at the host before requesting a TLS certificate. Keep ports
8081, 8082, and 8083 private; only Nginx should be reachable publicly.

## Install

Install Python 3.12, Node.js 22, Nginx, Certbot, Git, and curl. On Ubuntu
24.04, Python 3.12 is provided by the distribution. Follow the
[Node.js 22 installation instructions](https://github.com/nodesource/distributions#installation-instructions)
and [Certbot instructions](https://certbot.eff.org/instructions) for that host.

```bash
sudo git clone https://github.com/TheAppliedScientist/TheAppliedScientist.git /opt/theappliedscientist
cd /opt/theappliedscientist
sudo ./tas setup search reviewer --runtime native --non-interactive --skip-index
sudo python3.12 -m venv .venvs/hosted
sudo .venvs/hosted/bin/pip install -r components/hosted-access/requirements.txt
```

Copy `search.env.example`, `review.env.example`, and `gateway.env.example` to
`/etc/theappliedscientist-search.env`, `/etc/theappliedscientist-review.env`,
and `/etc/theappliedscientist-gateway.env`. Set the Gemini and Anthropic-format
keys, replace the gateway secret with a random 32+ character value, and set
`HOSTED_PUBLIC_HOST` to your DNS name. Keep these three files root-owned and
mode `0600`. Do not put keys in the repository or the Nginx configuration.
Use an Anthropic Messages endpoint for Claude Code; an OpenAI-only endpoint
needs a converter. The Reviewer defaults match the pinned local Reviewer setup.

Create three dedicated system users: `tas-search`, `tas-reviewer`, and
`tas-gateway`. Their writable homes are the matching subdirectories under
`/var/lib/theappliedscientist`. Give each user ownership only of its own
subdirectory; the parent directory should be root-owned and traversable.
Install the four `.service` files from this folder into
`/etc/systemd/system/`. Keep `review-paths.env` and `gateway-paths.env` in
`/opt/theappliedscientist/deploy/hosted/`; the units load them after the
private key files. Replace `HOSTNAME` in
`nginx.conf.example` with your DNS name and install it as an enabled Nginx
site. Check with `nginx -t`, then reload Nginx and request a certificate with
Certbot's Nginx plugin.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tas-index tas-search tas-reviewer tas-gateway
```

The index download is about 13 GB. Until it completes, `/health` reports
`starting`, searches return HTTP 503, and submitted reviews remain queued.
The download resumes after an interruption. Check `journalctl -u tas-index
-f` and `systemctl status tas-index` for progress.

## Check the public service

```bash
curl -fsS https://YOUR-HOST/health
curl -fsS https://YOUR-HOST/docs
```

After the index is ready, run a real Search request through `/api/search`,
then submit a real LaTeX manuscript to `/api/reviews` and poll its returned
job ID. A successful health check alone does not test Gemini, Claude Code,
or the review prompt. Test an MCP client against `https://YOUR-HOST/mcp` too.

The gateway runs one worker on the reference 2-core host. SQLite enforces one
active or queued review per IP and the configured daily cap. Nginx overwrites
`X-Real-IP`, which the gateway uses for HMAC-based per-IP accounting. Do not
expose port 8083 directly. Finished paper text is removed from the queue at
completion; the review result and archived trajectory are deleted after seven
days. The Reviewer cannot read the gateway queue or Search credentials. Its
own Anthropic key is still available to Claude Code, so use a dedicated key
with a spending limit. Rotate logs according to your site's retention policy.
