"""CLI entry point: start server and open browser."""

import argparse
import sys
import webbrowser

from aiohttp import web
from loguru import logger

from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="claude-rts terminal canvas")
    parser.add_argument("--port", type=int, default=3000, help="Server port (default: 3000)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    # Configure loguru: remove default handler, add our own
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="DEBUG",
    )
    logger.add(
        "claude-rts.log",
        rotation="10 MB",
        retention="3 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    )

    logger.info("claude-rts starting on http://localhost:{}", args.port)

    app = create_app()

    if not args.no_browser:
        async def open_browser(app):
            url = f"http://localhost:{args.port}"
            logger.info("Opening browser: {}", url)
            webbrowser.open(url)
        app.on_startup.append(open_browser)

    logger.info("Press Ctrl+C to stop")
    web.run_app(app, host="localhost", port=args.port, print=None)


if __name__ == "__main__":
    main()
