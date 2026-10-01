# HTTrack MCP Server — Help

Everything an operator or an AI agent needs to install, run, and use this server.

---

## 1. What this is

A Model Context Protocol (MCP) server that drives **HTTrack** (the classic website
downloader) and exposes the resulting archives for browsing, reading, and searching.
Everything runs in one Docker container; downloads land in a single volume you control.

## 2. Install

```bash
git clone https://github.com/TechVentures-Studio/httrack-mcp.git
cd httrack-mcp
docker build -t httrack-mcp-server .
```

## 3. Run

```bash
mkdir -p /srv/webarchive            # wherever you want downloads to live
docker run -d --name httrack-mcp --restart unless-stopped \
  -p 9050:8000 \
  -v /srv/webarchive:/data:rw \
  httrack-mcp
```

Check it is alive:

```bash
curl -s -X POST http://localhost:9050/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
# Expect an SSE response with serverInfo.name == "httrack"
```

## 4. Connect a client

Any MCP client that supports streamable HTTP:

- **URL:** `http://<docker-host>:9050/mcp`
- **Transport:** `streamable-http` (SSE-backed HTTP)
- **Auth:** none — run it on a trusted network or behind an authenticating gateway
  (e.g. MCPHub, which registers it as a server entry with `"type": "streamable-http"`).

## 5. Using the tools (agent cookbook)

### Mirror a site

```
mirror_site(url="https://docs.example.com", project="docs-example", depth=3)
→ {"job_id": "6bf630b9ff84", "project": "docs-example", "status": "started"}
```

Mirroring runs in the background. Poll with:

```
mirror_status(job_id="6bf630b9ff84")   → status: running | done | failed (+ log tail)
```

### Useful options

- `depth` — link depth (default 3, max 16). Small for docs sites; larger for archives.
- `filters` — HTTrack filter patterns, e.g. `["+*.css", "-*banner*"]`.
- `max_rate` — bandwidth cap, e.g. `"200K"` (be nice to small sites).

### Find what's in the archive

```
catalog()
→ [{"name": "docs-example", "description": "Example Docs — reference manual",
    "source_url": "https://docs.example.com", "status": "available",
    "html_pages": 412, "files": 1830, "size_mb": 96.4}, ...]
```

The short description is derived automatically from the site's `<title>` and meta
description, so an agent can pick a project without downloading anything.

### Read and search

```
list_files(project="docs-example", subpath="api/")
read_file(project="docs-example", path="api/index.html", max_bytes=65536)
search_files(project="docs-example", query="authentication", max_results=40)
```

`read_file` refuses binary files (images, PDFs, fonts...) and truncates large text at
`max_bytes` (hard cap 256 KB), returning `truncated: true` so agents know to read in
chunks. Downloads themselves land at `<volume>/<project>/…` — usable offline, viewable
in any browser.

## 6. Concurrency rules (read before automating)

1. **Different projects can run in parallel** — each mirror is an isolated `httrack`
   process with its own directory.
2. **One active mirror per project.** A second `mirror_site` call for the same project
   returns `{"status": "already_running", "job_id": ...}` — join the existing job by
   polling `mirror_status` instead of starting a new one. The same guard catches httrack
   processes started outside this server (e.g. from a GUI sharing the same volume).

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `mirror_status` shows `failed (exit 255)` and the log tail shows httrack's usage text | An invalid option was passed. Note: `--continue` / `--update` are standalone-mode flags for httrack and must **not** be combined with a URL — this server never passes them, but custom `filters` can break the command line. |
| Download is slower than expected | Use a larger `max_rate` (default `500K`) or raise `depth` carefully. |
| `read_file` says "binary file" | Intentional — fetch binaries from the volume mount directly (`<volume>/<project>/…`). |
| Port 9050 busy | Run with `-p <other>:8000` and point clients at the new port. |
| Client can't connect | Verify `POST /mcp` returns an SSE initialize response (see §3); check `-v /data` volume exists and is writable. |

## 8. Permissions and privacy notes

- The container runs as root inside (HTTrack convention); all files it writes on the
  volume follow your Docker/UMASK settings.
- **Legal:** mirroring websites is subject to the target site's terms and robots.txt.
  This server sets `--robots=0` by default for agent automation convenience — you are
  responsible for respecting site policies and copyright of downloaded content.

## 9. License

Copyright (C) 2026 Tech Ventures VCC. This program is free software under the
**GNU Affero General Public License v3.0** — see [LICENSE](LICENSE). If you run a modified
version as a network service for others, you must offer those users the corresponding
source (AGPL §13).