# claude-rts

RTS-style terminal canvas — a web-based multiplexer where devcontainer shells live as draggable, resizable cards on a pannable/zoomable 4K canvas.

## Quick Start

```bash
cd d:/containers/claude-rts
pip install -e .
python -m claude_rts          # starts server on :3000, opens browser
python -m claude_rts --port 3001 --no-browser  # custom port, no auto-open
```

## Architecture

- **Backend**: Python (aiohttp) serving a single HTML page and WebSocket endpoints
- **Frontend**: Single `index.html` with xterm.js, panzoom, interact.js (all via CDN, no build step)
- **Terminal bridge**: Each WebSocket connection spawns a `docker.exe exec -it` process via pywinpty (Windows ConPTY), giving full PTY support (colors, readline, resize)
- **Container discovery**: `docker.exe ps` with devcontainer label filtering

## File Structure

```
claude_rts/
  __init__.py
  __main__.py          # CLI: argparse, loguru setup, start server + open browser
  server.py            # aiohttp routes: GET /, GET /api/hubs, WS /ws/{hub}
  discovery.py         # docker.exe ps → list of {hub, container}
  static/
    index.html         # entire frontend (JS/CSS inline)
```

## Key Design Decisions

- **pywinpty for ConPTY**: `asyncio.create_subprocess_exec` only gives pipes (no PTY), so `docker.exe exec -it` fails or has no echo. pywinpty provides a real Windows ConPTY that docker can allocate a PTY against.
- **loguru for logging**: Verbose server-side logging to stderr + rotating file (`claude-rts.log`). Terminal I/O bytes are NOT logged to avoid noise.
- **Single HTML file**: All JS/CSS is inline in index.html. External libs (xterm.js, panzoom, interact.js) load from CDN. No npm, no bundler.
- **No container lifecycle management**: claude-rts only attaches to running containers. Starting/stopping containers is out of scope.

## Development

### Dependencies

- `aiohttp` — async HTTP + WebSocket server
- `pywinpty` — Windows ConPTY bindings (PTY for docker exec)
- `loguru` — structured logging

### Logging

Server ops are logged verbosely via loguru:
- stderr: colored, human-readable
- `claude-rts.log`: rotating file log (10 MB, 3 day retention)

Terminal data (stdout bytes) is intentionally NOT logged.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serves `static/index.html` |
| GET | `/api/hubs` | Returns JSON array of `{hub, container}` for running devcontainers |
| WS | `/ws/{hub}` | WebSocket bridge to `docker.exe exec -it -u vscode <container> bash -l` |

### WebSocket Protocol

- **Binary frames** (browser → server): terminal keystrokes (UTF-8 encoded)
- **Binary frames** (server → browser): terminal output
- **Text frames** (browser → server): JSON control messages, e.g. `{"type": "resize", "cols": 120, "rows": 40}`

## Roadmap

See [ROADMAP.md](ROADMAP.md) for milestones. M0–M4 are complete.
