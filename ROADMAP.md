# claude-rts — RTS-Style Terminal Canvas

A web-based terminal multiplexer where devcontainer shells live as draggable, resizable cards on a pannable/zoomable 4K canvas — like commanding units in an RTS game.

## MVP Assumptions

1. Claude does all configuration and setup — the user should not need to edit config files
2. User launches with a single script; browser auto-opens to the server; server dies when the script is terminated (Ctrl+C)
3. Minimap in the top-left corner showing current viewport position + dot markers for each terminal card
4. Canvas size defaults to 3840x2160 (4K)
5. Right-click on empty canvas opens a context menu with "Spawn terminal copy" — user picks a hub, new terminal card appears at the click position

## Architecture

```
Browser (localhost:3000)
  └─ 4K canvas (3840x2160, pan/zoom)                     [HTML/JS + xterm.js]
       ├─ Terminal card [hub_1] ─── WebSocket ───┐
       ├─ Terminal card [hub_2] ─── WebSocket ───┤
       ├─ Terminal card [hub_3] ─── WebSocket ───┤
       ├─ ...additional spawned copies           ┤
       │                                         │
       ├─ Minimap (top-left)                     │
       └─ Context menu (right-click)             │
                                                 │
Python server (aiohttp, localhost:3000)          │
  ├─ GET  /              → index.html            │
  ├─ GET  /api/hubs      → discovered hubs JSON  │
  └─ WS   /ws/{hub}  ←──────────────────────────┘
       └─ per connection:
            asyncio subprocess: docker.exe exec -it -u vscode <container> bash -l
            stdin:  browser keystrokes → process stdin
            stdout: process stdout → browser terminal
```

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Terminal rendering | xterm.js + xterm-addon-fit (CDN) | Industry standard, same as VS Code terminal |
| WebSocket relay | Python asyncio subprocess | No external binaries, full control |
| Canvas pan/zoom | panzoom (CDN) | Lightweight, smooth inertia, cursor-centered zoom |
| Drag/resize | interact.js (CDN) | Drag + resize + snapping in one library |
| Frontend | Single index.html, no build step | Served by the Python server |
| Backend | aiohttp | Async HTTP + WebSocket in one package |
| Container discovery | subprocess `docker.exe ps` | Simple, no SDK dependency |
| Launcher | `python -m claude_rts` (argparse) | Starts server, opens browser, Ctrl+C kills all |

## Milestones

### M0 — Single terminal in browser (plumbing)
- [ ] Project scaffolding: pyproject.toml, claude_rts package, static/index.html
- [ ] aiohttp server serves index.html on GET /
- [ ] WebSocket endpoint /ws/{hub} spawns `docker.exe exec -it -u vscode <container> bash -l`
- [ ] Bridge subprocess stdin/stdout over WebSocket binary frames
- [ ] index.html: load xterm.js from CDN, connect to /ws/{hub}, render terminal
- [ ] Handle PTY sizing: client sends JSON resize message, server adjusts (if possible)
- [ ] `python -m claude_rts` starts server on :3000 and opens browser
- [ ] Ctrl+C cleanly kills subprocess bridges (containers keep running)
- **Exit criteria**: Type `ls` in browser, see container filesystem

### M1 — Multi-terminal canvas with minimap
- [ ] /api/hubs endpoint: discover running devcontainers via docker.exe ps
- [ ] Frontend fetches /api/hubs on load, creates one terminal card per hub
- [ ] Cards absolute-positioned on a 3840x2160 canvas div
- [ ] Default layout: 3x2 grid centered on canvas
- [ ] Each card: title bar (hub name) + xterm.js + own WebSocket connection
- [ ] panzoom on the canvas container (wheel to zoom, drag empty space to pan)
- [ ] Minimap (top-left, ~200x112px):
  - [ ] Shows 4K canvas scaled down
  - [ ] Colored dot per terminal card at its position
  - [ ] Viewport rectangle showing current view
  - [ ] Click minimap to jump to that position
- [ ] interact.js: drag cards by title bar, resize by edges/corners
- [ ] xterm.js addon-fit re-fits on card resize
- [ ] Z-order: clicked card comes to front
- **Exit criteria**: All hubs visible on canvas, pannable, zoomable, minimap works

### M2 — Context menu and terminal spawning
- [ ] Right-click on empty canvas shows context menu at click position
- [ ] Menu item: "Spawn terminal" → submenu lists available hubs
- [ ] Selecting a hub spawns a new terminal card at the right-click position
- [ ] New card gets its own WebSocket + subprocess (independent shell session)
- [ ] Multiple cards can connect to the same hub (separate bash sessions)
- [ ] Close button (X) on card title bar: kills subprocess, removes card
- **Exit criteria**: Can right-click, spawn a hub_1 copy, use it independently

### M3 — Polish
- [ ] Card status indicator (green dot = connected, red = disconnected, spinner = connecting)
- [ ] Auto-reconnect on WebSocket drop (exponential backoff)
- [ ] Double-click title bar to zoom-to-fill (card takes full viewport)
- [ ] Zoom-to-fit button (frames all cards)
- [ ] Keyboard shortcuts: Ctrl+0 zoom-to-fit, Escape to deselect
- [ ] Save card positions/sizes to localStorage, restore on reload
- [ ] Dark theme (default), clean card styling
- **Exit criteria**: Feels like a real tool

### M4 — Settings menu
- [ ] Accessible settings panel (gear icon in status bar, or keyboard shortcut)
- [ ] Copy/paste configuration:
  - [ ] Choose copy shortcut (Ctrl+Shift+C, Ctrl+C when selection exists, or auto-copy on select)
  - [ ] Choose paste shortcut (Ctrl+Shift+V, Ctrl+V, right-click, or all)
  - [ ] Toggle right-click behavior (paste vs context menu)
- [ ] Settings persisted to localStorage
- [ ] Settings applied immediately (no reload required)
- **Exit criteria**: User can configure copy/paste to match their preferred workflow

## Open Questions

1. **PTY on Windows** — `asyncio.create_subprocess_exec` gives pipes, not a PTY. `docker.exe exec -it` may refuse without a real PTY. Options:
   - Use pywinpty to provide a ConPTY
   - Drop `-t`, send raw bytes (loses colors/readline — test if acceptable)
   - Test in M0, pick the approach that works

2. **Container lifecycle** — MVP is attach-only. Starting/stopping containers is out of scope.

3. **Canvas size** — Fixed 4K for MVP. Could make configurable or auto-scale later.

## File Structure

```
claude-rts/
  ROADMAP.md
  pyproject.toml
  claude_rts/
    __init__.py
    __main__.py          # CLI: parse args, start server, open browser
    server.py            # aiohttp app: static files, /api/hubs, /ws/{hub}
    discovery.py         # docker.exe ps parsing → list of {hub, container}
    static/
      index.html         # single-page canvas UI (all JS/CSS inline)
```
