#!/usr/bin/env python3
"""Resume Dashboard v2 - one-click session resume + play logs."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI(title="Resume Dashboard")

HOME = Path.home()
BASE = Path(__file__).parent

# ── resume history (in-memory play log) ───────────────────────────

play_log: list[dict[str, Any]] = []


def log_play(tool: str, sid: str, status: str, detail: str = ""):
    entry = {
        "ts": dt.datetime.now().isoformat(),
        "ts_fmt": dt.datetime.now().strftime("%H:%M:%S"),
        "tool": tool.upper(),
        "sid": sid,
        "status": status,
        "detail": detail,
    }
    play_log.append(entry)
    if len(play_log) > 50:
        play_log[:] = play_log[-50:]


# ── session scanners ──────────────────────────────────────────────

def _fmt_date(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%d,%b %I:%M %p").lstrip("0").replace(" 0", " ")

def scan_agy() -> list[dict[str, Any]]:
    sessions = []
    brain = HOME / ".gemini" / "antigravity-cli" / "brain"
    if brain.exists():
        for d in sorted(brain.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if d.is_dir() and not d.name.startswith("."):
                ts = d.stat().st_mtime
                sessions.append({
                    "id": d.name,
                    "title": d.name[:32],
                    "date": dt.datetime.fromtimestamp(ts).isoformat(),
                    "date_fmt": _fmt_date(ts),
                    "resume_cmd": f"agy --conversation {d.name}",
                })
    return sessions[:2]


def scan_claude() -> list[dict[str, Any]]:
    sessions = []
    for pdir in [HOME / ".claude" / "projects", HOME / ".claude-work" / "projects"]:
        if pdir.exists():
            for proj in pdir.iterdir():
                if proj.is_dir():
                    for j in sorted(proj.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
                        ts = j.stat().st_mtime
                        sessions.append({
                            "id": j.stem,
                            "title": proj.name,
                            "date": dt.datetime.fromtimestamp(ts).isoformat(),
                            "date_fmt": _fmt_date(ts),
                            "resume_cmd": f"claude resume {j.stem}",
                        })
    sessions.sort(key=lambda x: x["date"], reverse=True)
    return sessions[:2]


def scan_codex() -> list[dict[str, Any]]:
    sessions = []
    cdir = HOME / ".codex" / "sessions"
    if cdir.exists():
        for f in sorted(cdir.rglob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
            parts = f.stem.split("-")
            sid = "-".join(parts[-5:]) if len(parts) >= 5 else f.stem
            ts = f.stat().st_mtime
            sessions.append({
                "id": sid,
                "title": f.parent.name + "/" + f.stem[:20],
                "date": dt.datetime.fromtimestamp(ts).isoformat(),
                "date_fmt": _fmt_date(ts),
                "resume_cmd": f"codex resume {sid}",
            })
    return sessions[:2]


def scan_opencode() -> list[dict[str, Any]]:
    sessions = []
    try:
        res = subprocess.run(
            f"opencode session list -n 6",
            capture_output=True, text=True, shell=True, timeout=10,
        )
        for line in res.stdout.strip().split("\n"):
            if not line.strip() or line.startswith("Session") or line.startswith("─"):
                continue
            parts = line.split()
            if parts and parts[0].startswith("ses_"):
                sid = parts[0]
                raw = line[len(sid):].strip()
                m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", raw)
                title = raw[:m.start()].strip().rstrip("-").strip() if m else raw[:40]
                ts = None
                if m:
                    try:
                        ts = dt.datetime.fromisoformat(m.group(1)).timestamp()
                    except Exception:
                        pass
                sessions.append({
                    "id": sid,
                    "title": title[:48],
                    "date_fmt": _fmt_date(ts) if ts else (parts[-2] + " " + parts[-1] if len(parts) >= 2 else "--"),
                    "resume_cmd": f"opencode -s {sid}",
                })
    except Exception:
        pass
    return sessions[:2]


def scan_kimi() -> list[dict[str, Any]]:
    sessions = []
    for kdir in [HOME / ".kimi-code" / "sessions", HOME / ".kimi" / "sessions"]:
        if kdir.exists():
            for d in sorted(kdir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if d.is_dir() and not d.name.startswith("."):
                    sid = d.name
                    ts = d.stat().st_mtime
                    sessions.append({
                        "id": sid,
                        "title": sid[:32],
                        "date": dt.datetime.fromtimestamp(ts).isoformat(),
                        "date_fmt": _fmt_date(ts),
                        "resume_cmd": f"kimi -S {sid}",
                    })
    sessions.sort(key=lambda x: x["date"], reverse=True)
    return sessions[:2]


# ── proxy testers ─────────────────────────────────────────────────

DS_FREE_URL = "http://127.0.0.1:22217"
DS_FREE_REPO = HOME / "OneDrive" / "Desktop" / "ds-free-api"
DS_FREE_CONFIG = DS_FREE_REPO / "config.toml"
KIMI_PROXY_URL = "http://localhost:3000"


def _read_dsfree_key() -> str:
    try:
        import re
        text = DS_FREE_CONFIG.read_text(encoding="utf-8")
        match = re.search(r'(?s)\[\[api_keys\]\].*?key\s*=\s*"([^"]+)"', text)
        return match.group(1) if match else ""
    except Exception:
        return ""


def test_deepseek() -> dict[str, Any]:
    key = _read_dsfree_key()
    if not key:
        return {"status": "error", "detail": "ds-free-api config key not found"}
    try:
        import httpx
        r = httpx.get(
            f"{DS_FREE_URL}/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        if r.status_code == 200:
            models = r.json().get("data", [])
            names = [m.get("id", "") for m in models]
            return {"status": "ok", "detail": f"ds-free-api · {', '.join(names)}"}
        return {"status": "error", "detail": f"HTTP {r.status_code}: {r.text[:100]}"}
    except Exception as e:
        return {"status": "error", "detail": f"{DS_FREE_URL} unreachable: {str(e).split(':')[-1].strip()}"}


def test_kimi() -> dict[str, Any]:
    try:
        import httpx
        r = httpx.get(
            f"{KIMI_PROXY_URL}/models",
            headers={"Authorization": "Bearer Waguri"},
            timeout=5,
        )
        if r.status_code == 200:
            models = r.json().get("data", [])
            names = [m["id"] for m in models]
            return {"status": "ok", "detail": f"kimi-proxy · {', '.join(names)}"}
        return {"status": "error", "detail": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"status": "error", "detail": f"{KIMI_PROXY_URL} unreachable: {str(e).split(':')[-1].strip()}"}


# ── resume executor ───────────────────────────────────────────────

def _open_terminal(cmd: str, cwd: str | None = None):
    """Open a new terminal window running the command."""
    try:
        if cwd:
            full = f'Set-Location "{cwd}"; {cmd}'
        else:
            full = cmd
        subprocess.Popen(
            ["pwsh", "-NoExit", "-Command", full],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return True, ""
    except Exception as e:
        return False, str(e)


RESUME_MAP: dict[str, dict[str, Any]] = {
    "agy": {"label": "AGY", "cwd": None},
    "claude": {"label": "CLAUDE", "cwd": None},
    "codex": {"label": "CODEX", "cwd": str(BASE)},
    "opencode": {"label": "OPENCODE", "cwd": str(BASE)},
    "kimi": {"label": "KIMI", "cwd": None},
}


@app.post("/api/resume/{tool}/{session_id}")
def api_resume(tool: str, session_id: str):
    scanners = {
        "agy": ("agy", f"agy --conversation {session_id}", None),
        "claude": ("claude", f"claude resume {session_id}", None),
        "codex": ("codex", f"codex resume {session_id}", str(BASE)),
        "opencode": ("opencode", f"opencode -s {session_id}", str(BASE)),
        "kimi": ("kimi", f"kimi -S {session_id}", None),
    }
    info = scanners.get(tool)
    if not info:
        log_play(tool, session_id, "error", "unknown tool")
        return JSONResponse({"ok": False, "error": f"Unknown tool: {tool}"}, status_code=400)

    label, cmd, cwd = info
    ok, err = _open_terminal(cmd, cwd)
    if ok:
        log_play(tool, session_id, "launched", cmd)
        return {"ok": True, "tool": label, "sid": session_id, "cmd": cmd}
    log_play(tool, session_id, "error", err)
    return JSONResponse({"ok": False, "error": err}, status_code=500)


KIMI_PROXY_EXE = str(HOME / ".kimi-proxy" / "kimi-proxy.exe")
DS_FREE_EXE = str(DS_FREE_REPO / "ds-free-api.exe")


@app.post("/api/proxy/start/{name}")
def api_start_proxy(name: str):
    if name == "kimi":
        if not Path(KIMI_PROXY_EXE).exists():
            return JSONResponse({"ok": False, "error": "kimi-proxy.exe not found"}, status_code=404)
        ok, err = _open_terminal(f'"{KIMI_PROXY_EXE}"')
        if ok:
            log_play("proxy", "kimi", "launched", KIMI_PROXY_EXE)
            return {"ok": True, "name": "kimi"}
        return JSONResponse({"ok": False, "error": err}, status_code=500)
    if name == "deepseek":
        if not Path(DS_FREE_EXE).exists():
            return JSONResponse({"ok": False, "error": "ds-free-api.exe not found"}, status_code=404)
        cmd = f'$env:DS_DATA_DIR="{DS_FREE_REPO}"; $env:RUST_LOG="info"; "{DS_FREE_EXE}" -c "{DS_FREE_CONFIG}"'
        ok, err = _open_terminal(cmd, str(DS_FREE_REPO))
        if ok:
            log_play("proxy", "deepseek", "launched", DS_FREE_EXE)
            return {"ok": True, "name": "deepseek", "note": "started local ds-free-api"}
        return JSONResponse({"ok": False, "error": err}, status_code=500)
    return JSONResponse({"ok": False, "error": f"Unknown proxy: {name}"}, status_code=400)


@app.get("/api/log")
def get_log():
    return list(reversed(play_log))


# ── API routes ────────────────────────────────────────────────────

@app.get("/api/sessions")
def get_sessions(tool: str = Query("all", description="Tool name or 'all'")):
    scanners = {
        "agy": scan_agy,
        "claude": scan_claude,
        "codex": scan_codex,
        "opencode": scan_opencode,
        "kimi": scan_kimi,
    }
    if tool == "all":
        return {k: v() for k, v in scanners.items()}
    if tool in scanners:
        return {tool: scanners[tool]()}
    return {"error": f"Unknown tool: {tool}"}


@app.get("/api/proxies")
def get_proxies():
    return {
        "deepseek": test_deepseek(),
        "kimi": test_kimi(),
    }


# ── dashboard HTML ────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='4' fill='%2300f0ff'/><text x='16' y='22' text-anchor='middle' font-size='18' fill='%2305080f' font-weight='bold'>R</text></svg>">
<title>RESUME DASHBOARD</title>
<style>
  :root {
    --bg: #05080f;
    --panel: #0e1525;
    --border: #1a2340;
    --text: #c8d6e5;
    --muted: #4a5a7a;
    --cyan: #00f0ff;
    --green: #00ff9d;
    --amber: #ffb000;
    --red: #ff2a55;
    --pink: #ff2d8a;
    --purple: #b026ff;
    --gold: #ffd700;
    --hotpink: #ff1493;
    --lime: #32ff7e;
    --orange: #ff6348;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html {
    overflow-y: scroll;
    scrollbar-width: auto;
  }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    padding: 30px;
    min-height: 100vh;
    overflow-y: scroll;
    font-size: 20px;
  }
  ::-webkit-scrollbar { width: 10px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 5px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--muted); }
  .mono { font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace; }

  .header {
    display: flex; justify-content: space-between; align-items: center;
    border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px;
    background: linear-gradient(90deg, rgba(0,240,255,0.04) 0%, transparent 50%, rgba(255,45,138,0.04) 100%);
    margin: -30px -30px 30px -30px; padding: 30px 30px 20px 30px;
  }
  .header h1 {
    font-size: 34px; font-weight: 800; letter-spacing: 1px;
    background: linear-gradient(135deg, var(--cyan), var(--purple));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .header .subtitle { font-size: 16px; color: var(--muted); margin-top: 2px; }
  .header-controls { display: flex; gap: 8px; align-items: center; }
  .header-right { text-align: right; }
  .btn {
    background: rgba(255,255,255,0.03); border: 1px solid var(--border); color: var(--text);
    padding: 9px 20px; cursor: pointer; font-family: inherit; font-size: 15px;
    transition: all .15s; border-radius: 6px; letter-spacing: 0.5px;
  }
  .btn:hover { border-color: var(--cyan); color: var(--cyan); background: rgba(0,240,255,0.06); }
  .btn.primary { border-color: var(--cyan); color: var(--cyan); }
  .btn.primary:hover { background: var(--cyan); color: var(--bg); }

  .grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px; margin-bottom: 30px;
  }
  @media (max-width: 1200px) { .grid { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }

  .panel {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px; transition: border-color .25s, box-shadow .25s;
    display: flex; flex-direction: column; min-height: 240px;
  }
  .panel:hover {
    box-shadow: 0 0 30px rgba(0,240,255,0.04);
  }
  .panel.agy {
    border-left: 3px solid var(--cyan);
    --pc: var(--cyan);
  }
  .panel.claude {
    border-left: 3px solid var(--purple);
    --pc: var(--purple);
  }
  .panel.codex {
    border-left: 3px solid var(--green);
    --pc: var(--green);
  }
  .panel.opencode {
    border-left: 3px solid var(--amber);
    --pc: var(--amber);
  }
  .panel.kimi {
    border-left: 3px solid var(--pink);
    --pc: var(--pink);
  }
  .panel-title {
    font-size: 14px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase;
    margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center;
    padding-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .panel-title .count {
    font-size: 12px; color: var(--muted); font-weight: 400;
    background: rgba(255,255,255,0.04); padding: 2px 10px; border-radius: 10px;
  }
  .panel-title .hint {
    font-size: 10px; font-weight: 400; text-transform: none; letter-spacing: 0;
    color: var(--muted); opacity: 0.5;
    margin-left: 6px;
  }

  .session-list { flex: 1; display: flex; flex-direction: column; gap: 4px; }
  .session-row {
    padding: 12px 10px; border-radius: 6px;
    transition: background .15s; border: 1px solid transparent;
    border-bottom: 1px solid rgba(255,255,255,0.05);
  }
  .session-row:last-child { border-bottom: none; }
  .session-row:hover { background: rgba(255,255,255,0.04); border-color: rgba(255,255,255,0.06); }

  .session-row.latest {
    background: linear-gradient(135deg, rgba(255,215,0,0.08) 0%, rgba(255,20,147,0.05) 100%);
    border: 1px solid rgba(255,215,0,0.2);
    box-shadow: 0 0 20px rgba(255,215,0,0.04);
  }
  .session-row.latest:hover { background: linear-gradient(135deg, rgba(255,215,0,0.12) 0%, rgba(255,20,147,0.08) 100%); }
  .session-row.latest .session-date { color: var(--gold); }

  .session-top {
    display: flex; align-items: center; gap: 8px; min-height: 20px;
  }
  .session-date {
    font-size: 13px; min-width: 120px;
    font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
    color: var(--muted);
  }
  .session-row .session-top .date { color: var(--muted); }
  .session-row.latest .session-top .date { color: var(--gold); font-weight: 600; }



  .play-btn {
    font-size: 18px; cursor: pointer; padding: 4px 12px; border: none; border-radius: 4px;
    background: transparent; color: var(--muted);
    transition: all .2s; flex-shrink: 0; line-height: 1; font-family: inherit;
  }
  .play-btn:hover { color: var(--pc, var(--green)); background: rgba(255,255,255,0.06); transform: scale(1.2); }
  .play-btn:active { transform: scale(0.9); }
  .play-btn:active { transform: scale(0.92); }
  .play-btn.sending { color: var(--amber); background: rgba(255,176,0,0.1); animation: pulse 0.6s infinite; }
  .play-btn.done { color: var(--bg); background: var(--green); }
  .play-btn.fail { color: var(--bg); background: var(--red); }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  .empty { color: var(--muted); font-size: 14px; padding: 24px 10px; text-align: center; letter-spacing: 0.5px; }

  .proxy-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 30px; }
  @media (max-width: 500px) { .proxy-grid { grid-template-columns: 1fr; } }
  .proxy-card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 18px 20px; display: flex; align-items: center; gap: 14px;
  }
  .proxy-card .dot {
    width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
  }
  .proxy-card .dot.ok { background: var(--green); box-shadow: 0 0 12px rgba(0,255,157,0.3); }
  .proxy-card .dot.err { background: var(--red); box-shadow: 0 0 12px rgba(255,42,85,0.3); }
  .proxy-card .info { flex: 1; }
  .proxy-card .name { font-size: 15px; font-weight: 700; letter-spacing: 1px; }
  .proxy-card .detail { font-size: 13px; color: var(--muted); margin-top: 2px; }
  .proxy-card .label-tag { font-size: 10px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: rgba(255,255,255,0.15); }
  .proxy-start {
    background: transparent; border: 1px solid var(--border); color: var(--muted);
    padding: 6px 14px; cursor: pointer; font-size: 13px; border-radius: 4px;
    font-family: inherit; letter-spacing: 1px; transition: all .15s; flex-shrink: 0;
  }
  .proxy-start:hover { border-color: var(--green); color: var(--green); background: rgba(0,255,157,0.06); }
  .proxy-start:disabled { opacity: 0.5; cursor: default; }

  .section-title {
    font-size: 14px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase;
    color: var(--muted); margin-bottom: 14px; display: flex; align-items: center; gap: 10px;
  }
  .section-title::after {
    content: ''; flex: 1; height: 1px; background: linear-gradient(90deg, var(--border), transparent);
  }

  .log-section { margin-top: 4px; }
  .log-feed {
    background: #040810; border: 1px solid var(--border); border-radius: 8px;
    padding: 10px; max-height: 180px; overflow-y: auto;
    font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 14px;
  }
  .log-feed::-webkit-scrollbar { width: 6px; }
  .log-feed::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  .log-entry {
    display: flex; gap: 12px; padding: 4px 6px; border-radius: 3px;
    border-bottom: 1px solid rgba(255,255,255,0.02);
  }
  .log-entry:last-child { border-bottom: none; }
  .log-ts { color: var(--muted); min-width: 72px; }
  .log-tag { font-weight: 700; min-width: 60px; }
  .log-detail { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .log-ok { color: var(--green); }
  .log-error { color: var(--red); }

  .footer-bar {
    display: flex; justify-content: space-between; align-items: center;
    margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border);
    font-size: 14px; color: var(--muted);
  }
  .footer-bar .clear { cursor: pointer; }
  .footer-bar .clear:hover { color: var(--red); }

  .toolbar { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; align-items: center; }
  .toolbar .label { font-size: 14px; color: var(--muted); }
  .toolbar .jump-btn {
    background: transparent; border: 1px solid var(--border); color: var(--muted);
    padding: 5px 12px; cursor: pointer; font-size: 13px; border-radius: 4px;
    font-family: inherit; transition: all .15s;
  }
  .toolbar .jump-btn:hover { border-color: var(--cyan); color: var(--cyan); background: rgba(0,240,255,0.05); }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1 class="mono">RESUME DASHBOARD</h1>
    <div class="subtitle">click <span style="color:var(--green)">&#9654;</span> to resume any session in a new terminal</div>
  </div>
  <div class="header-right">
    <div style="display:flex;gap:8px;align-items:center">
      <button class="btn primary" onclick="refreshAll()">&#8635; REFRESH</button>
      <span class="mono" id="ts" style="font-size:11px;color:var(--muted)"></span>
    </div>
  </div>
</div>

<div class="toolbar">
  <span class="label">JUMP:</span>
  <button class="jump-btn" onclick="scrollTo('p-agy')">AGY</button>
  <button class="jump-btn" onclick="scrollTo('p-claude')">CLAUDE</button>
  <button class="jump-btn" onclick="scrollTo('p-codex')">CODEX</button>
  <button class="jump-btn" onclick="scrollTo('p-opencode')">OPENCODE</button>
  <button class="jump-btn" onclick="scrollTo('p-kimi')">KIMI</button>
</div>

<div class="grid" id="sessions-grid"></div>

<div class="section-title">PROXY STATUS</div>
<div class="proxy-grid" id="proxies-grid"></div>

<div class="section-title">PLAY LOG <span style="font-weight:400;color:var(--muted);font-size:13px">(live)</span></div>
<div class="log-feed" id="log-feed"></div>
<div class="footer-bar">
  <span class="mono" id="footer-ts"></span>
  <span class="clear" onclick="clearLog()">&#10005; clear</span>
</div>

<script>
const LABELS = {
  agy: 'Antigravity',
  claude: 'Claude Code',
  codex: 'Codex CLI',
  opencode: 'OpenCode',
  kimi: 'Kimi Code',
};
const ACCENTS = {
  agy: '#00f0ff', claude: '#b026ff', codex: '#00ff9d',
  opencode: '#ffb000', kimi: '#ff2d8a',
};
const TOOL_KEYS = ['agy','claude','codex','opencode','kimi'];

function scrollTo(id) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function refreshAll() {
  document.getElementById('ts').textContent = 'loading...';
  await Promise.all([loadSessions(), loadProxies(), loadLog()]);
  const n = new Date();
  document.getElementById('ts').textContent = n.toLocaleTimeString();
  document.getElementById('footer-ts').textContent = 'sync: ' + n.toLocaleString();
}

async function loadSessions() {
  try {
    const r = await fetch('/api/sessions');
    const data = await r.json();
    const grid = document.getElementById('sessions-grid');
    grid.innerHTML = '';

    for (const tool of TOOL_KEYS) {
      const sessions = data[tool] || [];
      const accent = ACCENTS[tool] || '#00f0ff';
      const panel = document.createElement('div');
      panel.className = 'panel ' + tool;
      panel.id = 'p-' + tool;

      let rows = '';
      if (sessions.length === 0) {
        rows = '<div class="empty">&#8212; no sessions found &#8212;</div>';
      } else {
        for (let i = 0; i < sessions.length; i++) {
          const s = sessions[i];
          const cls = i === 0 ? 'session-row latest' : 'session-row';
          const dateFmt = s.date_fmt || '--';
          const rawId = s.id;
          const title = s.title || rawId.slice(0, 36);
          const displayId = rawId.length > 24 ? rawId.slice(0, 22) + '..' : rawId;
          rows += `<div class="${cls}">
            <span class="session-date">${dateFmt}</span>
            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px;font-weight:600;font-family:'Cascadia Code','Fira Code',monospace" title="${rawId}">${title}</span>
            <span style="font-size:11px;color:var(--muted);margin:0 4px;font-family:monospace">${displayId}</span>
            <button class="play-btn" onclick="playResume('${tool}','${rawId}',this)" title="Resume session" style="--pc:${accent}">&#9655;</button>
          </div>`;
        }
      }

      panel.innerHTML = `<div class="panel-title" style="color:${accent}">
        ${LABELS[tool]}<span class="hint">sessions</span>
        <span class="count">${sessions.length}</span>
      </div><div class="session-list">${rows}</div>`;
      grid.appendChild(panel);
    }
  } catch (e) {
    document.getElementById('sessions-grid').innerHTML = `<div style="color:var(--red);padding:20px">Error: ${e.message}</div>`;
  }
}

async function playResume(tool, sid, btn) {
  btn.classList.remove('done','fail');
  btn.classList.add('sending');
  btn.textContent = '..';
  try {
    const r = await fetch('/api/resume/' + tool + '/' + encodeURIComponent(sid), { method: 'POST' });
    const data = await r.json();
    if (data.ok) {
      btn.classList.remove('sending');
      btn.classList.add('done');
      btn.textContent = 'OK';
      setTimeout(() => { btn.innerHTML = '&#9654;'; btn.classList.remove('done'); }, 1500);
    } else {
      throw new Error(data.error || 'unknown');
    }
  } catch (e) {
    btn.classList.remove('sending');
    btn.classList.add('fail');
    btn.textContent = '!!';
    setTimeout(() => { btn.innerHTML = '&#9654;'; btn.classList.remove('fail'); }, 2000);
  }
  loadLog();
}

async function loadProxies() {
  try {
    const r = await fetch('/api/proxies');
    const data = await r.json();
    const grid = document.getElementById('proxies-grid');
    grid.innerHTML = '';
    for (const [name, px] of Object.entries(data)) {
      const ok = px.status === 'ok';
      const card = document.createElement('div');
      card.className = 'proxy-card';
      const dot = '<div class="dot ' + (ok ? 'ok' : 'err') + '"></div>';
      const statusLabel = ok ? 'ONLINE' : 'OFFLINE';
      const info = '<div class="info"><div class="name" style="color:' + (ok ? '#00ff9d' : '#ff2a55') + '">' +
        name.toUpperCase() + ' <span class="label-tag">[' + statusLabel + ']</span></div>' +
        '<div class="detail">' + (px.detail || px.model || '') + '</div></div>';
      let btn = '';
      if (!ok) {
        btn = '<button class="proxy-start" onclick="startProxy(\'' + name + '\',this)">START</button>';
      }
      card.innerHTML = dot + info + btn;
      grid.appendChild(card);
    }
  } catch (e) {
    document.getElementById('proxies-grid').innerHTML = '<div style="color:var(--red)">Error: ' + e.message + '</div>';
  }
}

async function startProxy(name, btn) {
  btn.textContent = '...';
  btn.disabled = true;
  try {
    const r = await fetch('/api/proxy/start/' + name, { method: 'POST' });
    const data = await r.json();
    if (data.ok) {
      btn.textContent = 'OK';
      btn.style.borderColor = 'var(--green)';
      btn.style.color = 'var(--green)';
      setTimeout(loadProxies, 3000);
    } else {
      throw new Error(data.error || 'failed');
    }
  } catch (e) {
    btn.textContent = 'FAIL';
    btn.style.borderColor = 'var(--red)';
    btn.style.color = 'var(--red)';
    setTimeout(loadProxies, 4000);
  }
}

async function loadLog() {
  try {
    const r = await fetch('/api/log');
    const entries = await r.json();
    const feed = document.getElementById('log-feed');
    feed.innerHTML = entries.map(function(e) {
      return '<div class="log-entry">' +
        '<span class="log-ts">' + e.ts_fmt + '</span>' +
        '<span class="log-tag ' + (e.status === 'launched' ? 'log-ok' : 'log-error') + '">' + e.tool + '</span>' +
        '<span class="log-detail">' + (e.detail || e.sid) + '</span></div>';
    }).join('');
    feed.scrollTop = feed.scrollHeight;
  } catch (e) {}
}

function clearLog() {
  fetch('/api/log', { method: 'DELETE' }).then(loadLog).catch(function(){});
}

setInterval(loadLog, 3000);
refreshAll();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


@app.delete("/api/log")
def clear_log():
    play_log.clear()
    return {"ok": True}


if __name__ == "__main__":
    print("=" * 42)
    print("  RESUME DASHBOARD v2")
    print("  http://localhost:8765")
    print("")
    print("  > one-click session resume")
    print("  * most recent session highlighted")
    print("  > live play log")
    print("=" * 42)
    uvicorn.run(app, host="0.0.0.0", port=8765)
