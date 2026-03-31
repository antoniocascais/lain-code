"""lain-code: FastAPI backend for Claude Code session analytics."""

import asyncio
import logging
import os
import json
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from analyze_sessions import run_patterns, run_stats, build_analysis_prompt, parse_all

app = FastAPI()

DATA_DIR = os.environ.get("LAIN_DATA_DIR", os.path.expanduser("~/.claude/projects"))

# Per 1M tokens: (input, output, cache_read, cache_create)
# Source: https://docs.anthropic.com/en/docs/about-claude/models#model-pricing
MODEL_PRICING = {
    "claude-opus-4-6":   (5,    25, 0.5,  6.25),
    "claude-opus-4-5":   (5,    25, 0.5,  6.25),
    "claude-opus-4-1":   (15,   75, 1.5,  18.75),
    "claude-opus-4":     (15,   75, 1.5,  18.75),
    "claude-sonnet-4-6": (3,    15, 0.3,  3.75),
    "claude-sonnet-4-5": (3,    15, 0.3,  3.75),
    "claude-sonnet-4":   (3,    15, 0.3,  3.75),
    "claude-sonnet-3-7": (3,    15, 0.3,  3.75),
    "claude-haiku-4-5":  (1,     5, 0.1,  1.25),
    "claude-haiku-3-5":  (0.8,   4, 0.08, 1.0),
    "claude-opus-3":     (15,   75, 1.5,  18.75),
    "claude-haiku-3":    (0.25, 1.25, 0.03, 0.3),
}
FALLBACK_PRICING = (3, 15, 0.3, 3.75)

def _read_cwd_from_jsonl(files: list[Path]) -> str | None:
    """Read the cwd from the first JSONL entry that has one."""
    for f in files:
        try:
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    cwd = obj.get("cwd")
                    if cwd:
                        return cwd
        except (PermissionError, OSError, json.JSONDecodeError):
            continue
    return None


def friendly_name(cwd: str | None, folder: str) -> str:
    """Derive a short display name from the session's working directory."""
    if not cwd:
        return folder

    # Strip home prefix
    home_prefixes = ["/home/", "/Users/"]
    path = cwd
    for hp in home_prefixes:
        if path.startswith(hp):
            # Strip /home/<username>/
            rest = path[len(hp):]
            slash = rest.find("/")
            path = rest[slash:] if slash >= 0 else ""
            break

    # Strip common leading path segments
    for prefix in ("/.claude/projects/", "/git/ac/", "/git/indoc/", "/git/", "/Documents/", "/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break

    return path or folder


def _lookup_pricing(model: str):
    """Match model ID to pricing, stripping date suffixes if needed."""
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    for key in MODEL_PRICING:
        if model.startswith(key):
            return MODEL_PRICING[key]
    return FALLBACK_PRICING


def estimate_cost(input_t: int, output_t: int, cache_read: int, cache_create: int, model: str) -> float:
    p = _lookup_pricing(model)
    return (
        input_t * p[0] / 1_000_000
        + output_t * p[1] / 1_000_000
        + cache_read * p[2] / 1_000_000
        + cache_create * p[3] / 1_000_000
    )


def parse_session(filepath: str) -> dict | None:
    models = defaultdict(int)
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_create = 0
    first_ts = None
    last_ts = None
    session_id = None
    title = None

    try:
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = obj.get("timestamp")
                if ts:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts

                if not session_id:
                    session_id = obj.get("sessionId")

                if obj.get("type") == "custom-title":
                    title = obj.get("customTitle")

                if obj.get("type") == "assistant":
                    msg = obj.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    model = msg.get("model", "")
                    if model:
                        models[model] += 1
                    usage = msg.get("usage", {})
                    input_tokens += usage.get("input_tokens", 0)
                    output_tokens += usage.get("output_tokens", 0)
                    cache_read += usage.get("cache_read_input_tokens", 0)
                    cache_create += usage.get("cache_creation_input_tokens", 0)
    except (PermissionError, OSError):
        return None

    if not models:
        return None

    dominant_model = max(models, key=models.get)
    cost = estimate_cost(input_tokens, output_tokens, cache_read, cache_create, dominant_model)

    return {
        "session_id": session_id or Path(filepath).stem,
        "title": title,
        "date": first_ts[:10] if first_ts else None,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "models": dict(models),
        "api_calls": sum(models.values()),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_create_tokens": cache_create,
        "cost": round(cost, 4),
        "filepath": filepath,
    }


@app.get("/api/projects")
def get_projects():
    projects = {}
    base = Path(DATA_DIR)
    if not base.exists():
        return projects

    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        jsonl_files = list(d.rglob("*.jsonl"))
        if not jsonl_files:
            continue
        folder = d.name
        cwd = _read_cwd_from_jsonl(jsonl_files)
        projects[folder] = {
            "folder": folder,
            "name": friendly_name(cwd, folder),
            "sessions": len(jsonl_files),
        }
    return projects


@app.get("/api/stats")
def get_stats(
    projects: str = Query("", description="Comma-separated folder names"),
    start: str = Query("", description="Start date YYYY-MM-DD"),
    end: str = Query("", description="End date YYYY-MM-DD"),
):
    base = Path(DATA_DIR)
    if not base.exists():
        return {"api_calls": 0, "sessions": 0, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_create_tokens": 0, "models": {},
                "files_scanned": 0, "cost": 0, "sessions_list": []}
    selected = set(projects.split(",")) if projects else set()

    total_models = defaultdict(int)
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_create = 0
    total_cost = 0.0
    sessions_list = []
    files_scanned = 0

    for d in base.iterdir():
        if not d.is_dir():
            continue
        folder = d.name
        if selected and folder not in selected:
            continue

        jsonl_files = list(d.rglob("*.jsonl"))
        cwd = _read_cwd_from_jsonl(jsonl_files)
        display_name = friendly_name(cwd, folder)

        for f in jsonl_files:
            files_scanned += 1
            session = parse_session(str(f))
            if not session:
                continue

            if start and session["date"] and session["date"] < start:
                continue
            if end and session["date"] and session["date"] > end:
                continue

            for model, count in session["models"].items():
                total_models[model] += count

            total_input += session["input_tokens"]
            total_output += session["output_tokens"]
            total_cache_read += session["cache_read_tokens"]
            total_cache_create += session["cache_create_tokens"]
            total_cost += session["cost"]

            session["project"] = display_name
            session["project_folder"] = folder
            # Store path relative to DATA_DIR for the events API
            try:
                session["filepath"] = str(Path(session["filepath"]).relative_to(base))
            except ValueError:
                pass
            sessions_list.append(session)

    sessions_list.sort(key=lambda s: s.get("first_ts") or "", reverse=True)

    return {
        "api_calls": sum(total_models.values()),
        "sessions": len(sessions_list),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache_read,
        "cache_create_tokens": total_cache_create,
        "models": dict(total_models),
        "files_scanned": files_scanned,
        "cost": round(total_cost, 2),
        "sessions_list": sessions_list,
    }


def _resolve_jsonl(base: Path, fp: str) -> Path:
    """Resolve a relative path to a JSONL file under base, with traversal protection."""
    resolved = (base / fp).resolve()
    if not resolved.is_relative_to(base):
        raise HTTPException(status_code=400, detail=f"Invalid path: {fp}")
    if not resolved.is_file() or resolved.suffix != ".jsonl":
        raise HTTPException(status_code=404, detail=f"File not found: {fp}")
    return resolved


@app.get("/api/session/events")
def get_session_events(
    file: str = Query(..., description="JSONL file path relative to data dir"),
    after: int = Query(0, description="Return events after this line number"),
    limit: int = Query(500, description="Max events to return per request"),
):
    base = Path(DATA_DIR).resolve()
    filepath = _resolve_jsonl(base, file)

    try:
        with open(filepath) as f:
            total = 0
            events = []
            for line in f:
                total += 1
                if total <= after:
                    continue
                if len(events) >= limit:
                    total -= 1  # cursor = last processed line
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    events.append({"type": "error", "message": {"content": line}})
    except (PermissionError, OSError):
        raise HTTPException(status_code=500, detail="Failed to read session file")

    return {"events": events, "total": total}


CREDENTIALS_PATH = os.environ.get(
    "LAIN_CREDENTIALS",
    os.path.expanduser("~/.claude/.credentials.json"),
)


@app.get("/api/analyze/status")
def analyze_status():
    if Path(CREDENTIALS_PATH).is_file():
        return {"available": True}
    return {"available": False, "reason": "Claude credentials not found"}


class AnalyzeRequest(BaseModel):
    filepaths: list[str]


@app.post("/api/analyze")
async def analyze_sessions(req: AnalyzeRequest):
    if len(req.filepaths) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 sessions")
    if not req.filepaths:
        raise HTTPException(status_code=400, detail="No sessions selected")

    base = Path(DATA_DIR).resolve()
    paths: list[Path] = []
    for fp in req.filepaths:
        paths.append(_resolve_jsonl(base, fp))

    async def stream():
        try:
            parsed = await asyncio.to_thread(parse_all, paths)
            patterns = await asyncio.to_thread(run_patterns, paths, _parsed=parsed)
            stats = await asyncio.to_thread(run_stats, paths, _parsed=parsed)
            prompt = build_analysis_prompt(patterns, stats)

            try:
                from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage
                from claude_agent_sdk.types import StreamEvent

                async for msg in query(
                    prompt=prompt,
                    options=ClaudeAgentOptions(
                        permission_mode="bypassPermissions",
                        max_turns=1,
                        disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep"],
                        include_partial_messages=True,
                    ),
                ):
                    if isinstance(msg, StreamEvent):
                        event = msg.event
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                yield f"event: token\ndata: {json.dumps(text)}\n\n"
                    elif isinstance(msg, ResultMessage):
                        yield "event: done\ndata: {}\n\n"

            except ImportError as exc:
                yield f"event: token\ndata: {json.dumps(f'[claude-agent-sdk not available: {exc}]\\n\\n')}\n\n"
                yield f"event: token\ndata: {json.dumps(prompt)}\n\n"
                yield "event: done\ndata: {}\n\n"

        except Exception as e:
            logging.exception("Analyze stream error")
            yield f"event: error\ndata: {json.dumps({'message': 'Analysis failed'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return FileResponse("static/index.html")
