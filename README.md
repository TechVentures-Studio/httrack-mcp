# HTTrack MCP Server

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

A [FastMCP](https://gofastmcp.com) (Model Context Protocol) server that wraps the classic
[HTTrack](https://www.httrack.com) website copier, so AI agents can mirror websites into a
shared archive and then browse, read, and search the downloaded content — all over MCP.

**Copyright (C) 2026 [Tech Ventures VCC](https://github.com/TechVentures-Studio).**
Licensed under the **GNU Affero General Public License v3.0** (see [LICENSE](LICENSE)).

## Why

HTTrack is battle-tested for offline website copying, but it has no scripting API — only a
GUI and a CLI. This server makes it agent-native: an LLM agent can call `mirror_site` to
start a download in the background, poll `mirror_status`, and later answer questions from
the archive via `catalog`, `list_files`, `read_file`, and `search_files`.

## Tools

| Tool | Purpose |
|---|---|
| `mirror_site(url, project, depth, max_rate, filters)` | Start a background mirror into the archive (`/data/<project>`). One active mirror per project — a concurrent request for the same project returns `already_running` with the live `job_id`; different projects run in parallel. |
| `mirror_status(job_id)` | Job status (`running` / `done` / `failed` + log tail). Omit to list recent jobs; also accepts the project name. |
| `catalog()` | All projects: short name, short description (auto-derived from each site's `<title>`/meta description), source URL, status, page/file counts, size. |
| `list_files(project, subpath)` | Directory listing inside a project (name, type, size, modified). |
| `read_file(project, path, max_bytes)` | Read one text file (HTML/JS/CSS/txt/json/md). Refuses binaries; caps content at 256 KB to keep agent context sane. |
| `search_files(project, query, max_results)` | Case-insensitive substring search across a project's text files, capped results. |

## Quick start

Prerequisites: Docker.

```bash
docker build -t httrack-mcp .

docker run -d --name httrack-mcp --restart unless-stopped \
  -p 9050:8000 \
  -v /path/to/your/archive:/data:rw \
  httrack-mcp
```

The container exposes the MCP server over streamable HTTP at `http://<host>:9050/mcp`.

### Register in an MCP client

Example for MCPHub-style gateways (`streamable-http`):

```json
{
  "httrack": {
    "type": "streamable-http",
    "url": "http://192.168.1.35:9050/mcp",
    "enabled": true,
    "description": "HTTrack site mirroring + archive browsing",
    "owner": "admin"
  }
}
```

Clients that speak streamable HTTP directly can connect to `/mcp` with no extra auth
(nobody is authenticated; run this on a trusted network, or put it behind your gateway's
auth). See [HELP.md](HELP.md) for full usage, examples, and troubleshooting.

## Concurrency model

- One `httrack` process per project, isolated by its own output directory — no shared
  state between projects, so multiple agents can mirror different sites in parallel.
- `mirror_site` guards against double-mirroring the same project (in-process job table +
  a `/proc` scan for httrack processes writing to that project directory).

## CI

GitHub Actions runs on every PR and push: Python syntax check of `server.py`,
Dockerfile sanity assertions, and a Docker image build.

## License

This program is free software: you can redistribute it and/or modify it under the terms of
the GNU Affero General Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version.

If you run a **modified** version of this server as a network service, AGPL §13 requires
you to offer your users the corresponding source of that modified version.

Copyright (C) 2026 Tech Ventures VCC. HTTrack itself is GPL-licensed by Xavier Roche and
contributors; this project only shells out to it and is not affiliated with it.