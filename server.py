#!/usr/bin/env python3
# HTTrack MCP Server — FastMCP wrapper around the HTTrack website copier.
# Copyright (C) 2026 Tech Ventures VCC
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Network server notice (AGPL section 13): if you run a modified version of
# this server and users interact with it remotely, you must offer them the
# corresponding source of that modified version.
"""HTTrack MCP server: mirror sites + browse/search downloaded archives.

Runs inside a container with the Terramaster web archive mounted at /data
(same volume the WebHTTrack GUI writes to). Exposes streamable-http MCP at /mcp.
"""
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from fastmcp import FastMCP

BASE = Path("/data")
META_DIR = BASE / ".mcp-httrack"
LOG_DIR = META_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

mcp = FastMCP(
    "httrack",
    instructions=(
        "HTTrack website mirroring and archive browsing. mirror_site() downloads a "
        "website into a named project (background job). catalog() lists projects "
        "with short descriptions. list_files/read_file/search_files browse and read "
        "the downloaded content. Projects live under one shared archive."
    ),
)

JOBS: dict[str, dict] = {}  # job_id -> {proc, project, url, started, log}; project name -> same dict
BIN_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tgz",
    ".7z", ".rar", ".mp3", ".mp4", ".m4a", ".avi", ".mkv", ".woff", ".woff2", ".ttf",
    ".otf", ".eot", ".exe", ".dll", ".so", ".bin", ".deb", ".rpm", ".iso", ".sqlite",
    ".db", ".psd", ".xcf",
}


def _safe_project(project: str) -> str:
    p = (project or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", p):
        raise ValueError("project name must be 1-64 chars: letters, digits, dot, dash, underscore")
    return p


def _resolve(project: str, subpath: str) -> Path:
    root = (BASE / _safe_project(project)).resolve()
    target = (root / subpath.lstrip("/")).resolve() if subpath else root
    if not (str(target) == str(root) or str(target).startswith(str(root) + "/")):
        raise ValueError("path escapes project directory")
    if not target.exists():
        raise FileNotFoundError(f"not found: {subpath or project}")
    return target


def _running_projects() -> set[str]:
    procs = set()
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().split(b"\0")
            if cmd and cmd[0].endswith(b"httrack"):
                joined = b" ".join(cmd).decode("utf8", "replace")
                m = re.search(r"-O\s+(\S+?)(\s|$)", " " + joined[7:] + " ")
                if m:
                    procs.add(Path(m.group(1)).resolve().name)
        except OSError:
            continue
    return procs


def _job_status(job_id: str) -> dict:
    j = JOBS.get(job_id)
    if not j:
        return {"job_id": job_id, "status": "unknown"}
    alive = j["proc"].poll() is None
    log = Path(j["log"])
    tail = ""
    if log.exists():
        lines = log.read_text("utf8", "replace").splitlines()
        tail = "\n".join(lines[-8:])
    rc = None if alive else j["proc"].returncode
    return {
        "job_id": job_id,
        "project": j["project"],
        "url": j["url"],
        "status": "running" if alive else ("done" if rc == 0 else f"failed (exit {rc})"),
        "started": j["started"],
        "log_tail": tail,
    }


def _meta(project: str) -> dict:
    f = BASE / _safe_project(project) / ".httrack-mcp.json"
    if f.exists():
        try:
            return json.loads(f.read_text("utf8", "replace"))
        except Exception:
            return {}
    return {}


def _describe(project_dir: Path, meta: dict) -> str:
    """Short description: <title> + meta description from the project index page."""
    idx = next((project_dir / n for n in ("index.html", "index.htm") if (project_dir / n).is_file()), None)
    if not idx:
        return meta.get("url", "")
    try:
        html = idx.read_text("utf8", "replace")[:200_000]
    except OSError:
        return meta.get("url", "")
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I | re.S)
    if not desc:
        desc = re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', html, re.I | re.S)
    parts = []
    if title:
        parts.append(re.sub(r"\s+", " ", title.group(1)).strip()[:120])
    if desc:
        parts.append(re.sub(r"\s+", " ", desc.group(1)).strip()[:200])
    return " — ".join(parts) if parts else meta.get("url", "")


@mcp.tool
def mirror_site(
    url: str,
    project: str,
    depth: int = 3,
    max_rate: str = "500K",
    filters: list[str] | None = None,
) -> dict:
    """Mirror a website into the archive (background job). Returns job_id + paths.
    depth = link depth (0..16); filters = optional HTTrack filter expressions (+/- patterns).
    Concurrency: one active mirror per project — a second mirror_site() on the same
    project is rejected with a pointer to the running job; different projects run in parallel."""
    project = _safe_project(project)
    if not re.match(r"^https?://", url):
        raise ValueError("url must start with http:// or https://")
    # per-project concurrency guard
    active = JOBS.get(project)
    if active and active["proc"].poll() is None:
        return {
            "status": "already_running",
            "project": project,
            "job_id": next((k for k, v in JOBS.items() if v is active and k != project), None),
            "started": active["started"],
            "note": "poll mirror_status(job_id) for progress; the existing mirror was left untouched",
        }
    if project in _running_projects():
        raise ValueError(
            f"project '{project}' already has a running httrack process (started outside MCP); "
            "wait for it to finish before re-mirroring"
        )
    dest = BASE / project
    dest.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    log = LOG_DIR / f"{job_id}.log"
    cmd = [
        "httrack", url,
        "-O", str(dest),
        "--mirror", "-q", "-%v",
        f"--depth={max(0, min(int(depth), 16))}",
        f"--max-rate={max_rate}",
        "--robots=0",
    ] + list(filters or [])
    meta = {"job_id": job_id, "url": url, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    (dest / ".httrack-mcp.json").write_text(json.dumps(meta), "utf8")
    with open(log, "w") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    entry = {"proc": proc, "project": project, "url": url, "started": meta["started"], "log": str(log)}
    JOBS[job_id] = entry
    JOBS[project] = entry  # per-project alias for the concurrency guard
    return {"job_id": job_id, "project": project, "archive_path": str(dest), "status": "started"}


@mcp.tool
def mirror_status(job_id: str = "") -> dict:
    """Status of a mirror job (by job_id OR project name), or all recent jobs if omitted."""
    if job_id:
        j = JOBS.get(job_id)
        if j is None and JOBS.get(job_id) is None:
            return _job_status(job_id)
        return _job_status(job_id)
    running = {k: _job_status(k) for k, v in JOBS.items() if v["proc"].poll() is None and k != v["project"]}
    finished = {k: _job_status(k) for k, v in JOBS.items() if v["proc"].poll() is not None and k != v["project"]}
    finished = dict(sorted(finished.items())[-10:])
    return {"running": running, "recent_finished": finished}


@mcp.tool
def catalog() -> list[dict]:
    """List all mirrored projects: name, short description, source URL, status, size, pages."""
    out = []
    running = _running_projects()
    for d in sorted(p for p in BASE.iterdir() if p.is_dir() and not p.name.startswith(".")):
        meta = _meta(d.name)
        files = sum(1 for _ in d.rglob("*") if _.is_file())
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file() and f.stat().st_size > 0)
        html = sum(1 for f in d.rglob("*") if f.is_file() and f.suffix.lower() in (".html", ".htm"))
        out.append({
            "name": d.name,
            "description": _describe(d, meta),
            "source_url": meta.get("url", ""),
            "status": "mirroring" if d.name in running else "available",
            "last_modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(d.stat().st_mtime)),
            "files": files,
            "html_pages": html,
            "size_mb": round(size / 1_048_576, 1),
        })
    return out


@mcp.tool
def list_files(project: str, subpath: str = "") -> list[dict]:
    """List a directory inside a mirrored project (name, type, size, mtime)."""
    t = _resolve(project, subpath)
    if t.is_file():
        t = t.parent
    out = []
    for e in sorted(t.iterdir(), key=lambda x: x.name.lower()):
        if e.name.startswith(".httrack-mcp"):
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        out.append({
            "name": e.name,
            "type": "dir" if e.is_dir() else "file",
            "size": st.st_size,
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
        })
    return out[:500]


@mcp.tool
def read_file(project: str, path: str, max_bytes: int = 65536) -> dict:
    """Read one downloaded text file (HTML/JS/CSS/txt/json/md...). Binary types are refused.
    Returns content (truncated at max_bytes) with encoding note."""
    t = _resolve(project, path)
    if not t.is_file():
        raise ValueError("path is a directory; use list_files")
    if t.suffix.lower() in BIN_EXTS:
        raise ValueError(f"binary file ({t.suffix}); not readable over MCP")
    data = t.read_bytes()
    if b"\x00" in data[:8192]:
        raise ValueError("looks binary; not readable over MCP")
    total = len(data)
    data = data[: max(1, min(int(max_bytes), 262144))]
    return {
        "path": path,
        "total_bytes": total,
        "returned_bytes": len(data),
        "truncated": total > len(data),
        "content": data.decode("utf8", "replace"),
    }


@mcp.tool
def search_files(project: str, query: str, max_results: int = 40) -> list[dict]:
    """Search text inside a mirrored project (case-insensitive substring). Returns file:line matches."""
    root = _resolve(project, "")
    hits, buf = [], []
    rx = re.compile(re.escape(query), re.I)
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix.lower() in BIN_EXTS or f.name.startswith(".httrack-mcp"):
            continue
        try:
            if b"\x00" in f.read_bytes()[:8192]:
                continue
            for i, line in enumerate(f.read_text("utf8", "replace").splitlines(), 1):
                if rx.search(line):
                    buf.append({
                        "file": str(f.relative_to(root)),
                        "line": i,
                        "text": line.strip()[:300],
                    })
                    if len(buf) >= int(max_results):
                        break
        except (OSError, UnicodeDecodeError):
            continue
        if len(buf) >= int(max_results):
            break
    return buf


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)