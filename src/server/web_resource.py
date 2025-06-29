from aiohttp import web
import asyncio
import logging

app_logger = logging.getLogger(__name__)

class WebServer:
    def __init__(self, port: int, host: str = "0.0.0.0"):
        self.port = port
        self.host = host
        self.app = web.Application()
        self.runner = None
        self.site = None

    async def start(self):
        """Starts the web server."""
        if self.runner:
            app_logger.warning("Web server is already running.")
            return

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        app_logger.info(f"Web server started on http://{self.host}:{self.port}")

    async def stop(self):
        """Stops the web server."""
        if not self.runner:
            app_logger.warning("Web server is not running.")
            return

        await self.runner.cleanup()
        self.runner = None
        self.site = None
        app_logger.info("Web server stopped.")

    def add_route(self, path: str, handler):
        """Adds a route to the web application."""
        self.app.router.add_route("*", path, handler)

    async def cleanup(self):
        """Ensures the server is stopped during cleanup."""
        await self.stop() 