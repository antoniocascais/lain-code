# lain-code

Analytics dashboard for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions.

Parses JSONL conversation logs from `~/.claude/projects/` and serves a web UI with:

- **Model usage breakdown** — doughnut chart showing distribution across Opus, Sonnet, Haiku
- **Token stats** — input, output, cache read, cache create per session
- **Cost estimation** — estimated API value based on Anthropic pricing (not actual spend)
- **Project filtering** — sidebar with search, select/deselect all
- **Date range filtering** — presets (today, 7d, 30d) or custom range
- **Sortable sessions table** — all metrics per session

Dark CRT aesthetic inspired by Serial Experiments Lain.

## Quick start

```bash
make build
make serve
```

Open http://localhost:8000

## Requirements

- Docker + Docker Compose
- `~/.claude/projects/` directory (created by Claude Code)

Everything runs locally — no telemetry, no session data leaves your machine. The frontend loads Google Fonts and Chart.js from CDN (standard browser requests, no app data sent).

## How it works

The app runs in a Docker container with `~/.claude/projects/` mounted read-only at `/data`. A FastAPI backend scans the JSONL session files (including `subagents/` subdirectories) and serves both the API and the static frontend.

## Stack

- **Backend**: Python / FastAPI
- **Frontend**: Vanilla JS, Chart.js (CDN)
- **Container**: python:3.12-slim, non-root by default
