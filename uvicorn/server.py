from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import signal
import socket
import sys
import threading
import time
from collections.abc import Generator, Sequence
from email.utils import formatdate
from types import FrameType
from typing import TYPE_CHECKING, TypeAlias

import click

from uvicorn._compat import asyncio_run
from uvicorn.config import Config

if TYPE_CHECKING:
    from uvicorn.protocols.http.h11_impl import H11Protocol
    from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol
    from uvicorn.protocols.websockets.websockets_impl import WebSocketProtocol
    from uvicorn.protocols.websockets.websockets_sansio_impl import WebSocketsSansIOProtocol
    from uvicorn.protocols.websockets.wsproto_impl import WSProtocol

    Protocols: TypeAlias = H11Protocol | HttpToolsProtocol | WSProtocol | WebSocketProtocol | WebSocketsSansIOProtocol

HANDLED_SIGNALS = (
    signal.SIGINT,  # Unix signal 2. Sent by Ctrl+C.
    signal.SIGTERM,  # Unix signal 15. Sent by `kill <pid>`.
)
if sys.platform == "win32":  # pragma: py-not-win32
    HANDLED_SIGNALS += (signal.SIGBREAK,)  # Windows signal 21. Sent by Ctrl+Break.

logger = logging.getLogger("uvicorn.error")


class ServerState:
    """
    Shared servers state that is available between all protocol instances.
    """

    __slots__ = (
        "total_requests",
        "connections",
        "tasks",
        "default_headers",
        "_event_pool",
        "_event_pool_lock",
        "_event_pool_max_size",
        "_shutdown_event",
        "_cached_date",
        "_cached_date_time",
    )

    def __init__(self) -> None:
        self.total_requests = 0
        self.connections: set[Protocols] = set()
        self.tasks: set[asyncio.Task[None]] = set()
        self.default_headers: list[tuple[bytes, bytes]] = []

        # Event pool for performance optimization
        self._event_pool: list[asyncio.Event] = []
        self._event_pool_lock = asyncio.Lock()
        self._event_pool_max_size = 1000  # Cap pool size to prevent unbounded growth

        # Shutdown event for event-driven main loop (Phase 2a)
        self._shutdown_event = asyncio.Event()

        # Date header cache (Phase 2b)
        self._cached_date: bytes | None = None
        self._cached_date_time: int = 0

    def acquire_event(self) -> asyncio.Event:
        """
        Get an event from the pool or create a new one.

        Returns a cleared Event object ready for use. This reduces memory
        allocations and GC pressure under high load.
        """
        # Try to get from pool (non-blocking check)
        if self._event_pool:
            try:
                # Use try/except to handle race condition without async lock
                event = self._event_pool.pop()
                event.clear()
                return event
            except IndexError:
                pass  # Pool was emptied by another thread, create new

        return asyncio.Event()

    def release_event(self, event: asyncio.Event) -> None:
        """
        Return an event to the pool for reuse.

        The event is cleared and added back to the pool if there's space.
        This method is non-blocking to avoid performance overhead.
        """
        if len(self._event_pool) < self._event_pool_max_size:
            event.clear()
            self._event_pool.append(event)

    def get_date_header(self) -> bytes:
        """
        Get cached date header or generate new one if second changed.

        This method is thread-safe and can be called from any protocol.
        Returns the current date in HTTP header format (GMT).

        Phase 2b optimization: Caches the formatted date string and only
        regenerates when the second changes, reducing formatdate() calls.
        """
        current_time = int(time.time())

        # Check if we need to update the cache
        if current_time != self._cached_date_time or self._cached_date is None:
            self._cached_date = formatdate(current_time, usegmt=True).encode()
            self._cached_date_time = current_time

        return self._cached_date


class Server:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.server_state = ServerState()

        self.started = False
        self.should_exit = False
        self.force_exit = False
        self.last_notified = 0.0

        self._captured_signals: list[int] = []

    def run(self, sockets: list[socket.socket] | None = None) -> None:
        return asyncio_run(self.serve(sockets=sockets), loop_factory=self.config.get_loop_factory())

    async def serve(self, sockets: list[socket.socket] | None = None) -> None:
        with self.capture_signals():
            await self._serve(sockets)

    async def _serve(self, sockets: list[socket.socket] | None = None) -> None:
        process_id = os.getpid()

        config = self.config
        if not config.loaded:
            config.load()

        self.lifespan = config.lifespan_class(config)

        message = "Started server process [%d]"
        color_message = "Started server process [" + click.style("%d", fg="cyan") + "]"
        logger.info(message, process_id, extra={"color_message": color_message})

        await self.startup(sockets=sockets)
        if self.should_exit:
            return
        await self.main_loop()
        await self.shutdown(sockets=sockets)

        message = "Finished server process [%d]"
        color_message = "Finished server process [" + click.style("%d", fg="cyan") + "]"
        logger.info(message, process_id, extra={"color_message": color_message})

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await self.lifespan.startup()
        if self.lifespan.should_exit:
            self.should_exit = True
            return

        config = self.config

        def create_protocol(
            _loop: asyncio.AbstractEventLoop | None = None,
        ) -> asyncio.Protocol:
            return config.http_protocol_class(  # type: ignore[call-arg]
                config=config,
                server_state=self.server_state,
                app_state=self.lifespan.state,
                _loop=_loop,
            )

        loop = asyncio.get_running_loop()

        listeners: Sequence[socket.SocketType]
        if sockets is not None:  # pragma: full coverage
            # Explicitly passed a list of open sockets.
            # We use this when the server is run from a Gunicorn worker.

            def _share_socket(
                sock: socket.SocketType,
            ) -> socket.SocketType:  # pragma py-not-win32
                # Windows requires the socket be explicitly shared across
                # multiple workers (processes).
                from socket import fromshare  # type: ignore[attr-defined]

                sock_data = sock.share(os.getpid())  # type: ignore[attr-defined]
                return fromshare(sock_data)

            self.servers: list[asyncio.base_events.Server] = []
            for sock in sockets:
                is_windows = platform.system() == "Windows"
                if config.workers > 1 and is_windows:  # pragma: py-not-win32
                    sock = _share_socket(sock)  # type: ignore[assignment]
                server = await loop.create_server(create_protocol, sock=sock, ssl=config.ssl, backlog=config.backlog)
                self.servers.append(server)
            listeners = sockets

        elif config.fd is not None:  # pragma: py-win32
            # Use an existing socket, from a file descriptor.
            sock = socket.fromfd(config.fd, socket.AF_UNIX, socket.SOCK_STREAM)
            server = await loop.create_server(create_protocol, sock=sock, ssl=config.ssl, backlog=config.backlog)
            assert server.sockets is not None  # mypy
            listeners = server.sockets
            self.servers = [server]

        elif config.uds is not None:  # pragma: py-win32
            # Create a socket using UNIX domain socket.
            uds_perms = 0o666
            if os.path.exists(config.uds):
                uds_perms = os.stat(config.uds).st_mode  # pragma: full coverage
            server = await loop.create_unix_server(
                create_protocol, path=config.uds, ssl=config.ssl, backlog=config.backlog
            )
            os.chmod(config.uds, uds_perms)
            assert server.sockets is not None  # mypy
            listeners = server.sockets
            self.servers = [server]

        else:
            # Standard case. Create a socket from a host/port pair.
            try:
                server = await loop.create_server(
                    create_protocol,
                    host=config.host,
                    port=config.port,
                    ssl=config.ssl,
                    backlog=config.backlog,
                )
            except OSError as exc:
                logger.error(exc)
                await self.lifespan.shutdown()
                sys.exit(1)

            assert server.sockets is not None
            listeners = server.sockets
            self.servers = [server]

        if sockets is None:
            self._log_started_message(listeners)
        else:
            # We're most likely running multiple workers, so a message has already been
            # logged by `config.bind_socket()`.
            pass  # pragma: full coverage

        self.started = True

    def _log_started_message(self, listeners: Sequence[socket.SocketType]) -> None:
        config = self.config

        if config.fd is not None:  # pragma: py-win32
            sock = listeners[0]
            logger.info(
                "Uvicorn running on socket %s (Press CTRL+C to quit)",
                sock.getsockname(),
            )

        elif config.uds is not None:  # pragma: py-win32
            logger.info("Uvicorn running on unix socket %s (Press CTRL+C to quit)", config.uds)

        else:
            addr_format = "%s://%s:%d"
            host = "0.0.0.0" if config.host is None else config.host
            if ":" in host:
                # It's an IPv6 address.
                addr_format = "%s://[%s]:%d"

            port = config.port
            if port == 0:
                port = listeners[0].getsockname()[1]

            protocol_name = "https" if config.ssl else "http"
            message = f"Uvicorn running on {addr_format} (Press CTRL+C to quit)"
            color_message = "Uvicorn running on " + click.style(addr_format, bold=True) + " (Press CTRL+C to quit)"
            logger.info(
                message,
                protocol_name,
                host,
                port,
                extra={"color_message": color_message},
            )

    async def main_loop(self) -> None:
        """
        Event-driven main loop with no polling.

        Phase 2a optimization: Replaced polling loop with event-driven
        architecture. Main loop now waits on shutdown event instead of
        waking every 100ms. Background tasks handle date updates, callbacks,
        and request limit checking independently.
        """
        # Start background tasks
        tasks = []

        # Date header update task (runs every second)
        date_task = asyncio.create_task(self._update_date_header_loop())
        tasks.append(date_task)

        # Callback notify task (if configured)
        if self.config.callback_notify is not None:
            notify_task = asyncio.create_task(self._notify_callback_loop())
            tasks.append(notify_task)

        # Max requests monitoring task (if configured)
        if self.config.limit_max_requests is not None:
            max_requests_task = asyncio.create_task(self._check_max_requests())
            tasks.append(max_requests_task)

        try:
            # Wait for shutdown signal (event-driven, no polling)
            await self.server_state._shutdown_event.wait()
        finally:
            # Cancel all background tasks
            for task in tasks:
                if not task.done():
                    task.cancel()

            # Wait for tasks to finish cancellation with timeout
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    pass  # Tasks didn't finish in time, that's okay

    async def _update_date_header_loop(self) -> None:
        """
        Update date header exactly once per second.

        Phase 2a optimization: Runs in background, triggered by time
        rather than polling. Updates happen at second boundaries for
        precise timing.
        """
        try:
            while not self.should_exit:
                # Get cached or fresh date (Phase 2b: cache handles invalidation)
                current_date = self.server_state.get_date_header()

                if self.config.date_header:
                    date_header = [(b"date", current_date)]
                else:
                    date_header = []

                self.server_state.default_headers = date_header + self.config.encoded_headers

                # Calculate time until next second boundary
                current_time = time.time()
                next_update = int(current_time) + 1
                wait_time = next_update - time.time()

                # Wait until next second or shutdown
                try:
                    await asyncio.wait_for(
                        self.server_state._shutdown_event.wait(),
                        timeout=max(0.001, wait_time)
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    continue  # Time to update again
        except asyncio.CancelledError:
            pass  # Task cancelled, exit gracefully

    async def _notify_callback_loop(self) -> None:
        """
        Call notify callback at specified intervals.

        Phase 2a optimization: Runs independently of main loop and date
        updates. Only created if callback_notify is configured.
        """
        try:
            while not self.should_exit:
                current_time = time.time()

                # Check if it's time to notify
                if current_time - self.last_notified > self.config.timeout_notify:
                    await self.config.callback_notify()
                    self.last_notified = current_time

                # Wait for next notification time or shutdown
                wait_time = self.config.timeout_notify - (current_time - self.last_notified)

                try:
                    await asyncio.wait_for(
                        self.server_state._shutdown_event.wait(),
                        timeout=max(0.001, wait_time)
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    continue  # Time to notify again
        except asyncio.CancelledError:
            pass  # Task cancelled, exit gracefully

    async def _check_max_requests(self) -> None:
        """
        Monitor for max requests limit (if configured).

        Phase 2a optimization: Runs as a background task when
        limit_max_requests is set. Checks frequently to detect
        limit as soon as it's reached.
        """
        try:
            while not self.should_exit:
                if self.server_state.total_requests >= self.config.limit_max_requests:
                    logger.warning(
                        f"Maximum request limit of {self.config.limit_max_requests} exceeded. "
                        "Terminating process."
                    )
                    self.should_exit = True
                    self.server_state._shutdown_event.set()
                    break

                # Check every 100ms for responsiveness (matches old behavior)
                try:
                    await asyncio.wait_for(
                        self.server_state._shutdown_event.wait(),
                        timeout=0.1
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    continue  # Check again
        except asyncio.CancelledError:
            pass  # Task cancelled, exit gracefully

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        logger.info("Shutting down")

        # Stop accepting new connections.
        for server in self.servers:
            server.close()
        for sock in sockets or []:
            sock.close()  # pragma: full coverage

        # Request shutdown on all existing connections.
        for connection in list(self.server_state.connections):
            connection.shutdown()
        await asyncio.sleep(0.1)

        # When 3.10 is not supported anymore, use `async with asyncio.timeout(...):`.
        try:
            await asyncio.wait_for(
                self._wait_tasks_to_complete(),
                timeout=self.config.timeout_graceful_shutdown,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Cancel %s running task(s), timeout graceful shutdown exceeded",
                len(self.server_state.tasks),
            )
            for t in self.server_state.tasks:
                t.cancel(msg="Task cancelled, timeout graceful shutdown exceeded")

        # Send the lifespan shutdown event, and wait for application shutdown.
        if not self.force_exit:
            await self.lifespan.shutdown()

    async def _wait_tasks_to_complete(self) -> None:
        # Wait for existing connections to finish sending responses.
        if self.server_state.connections and not self.force_exit:
            msg = "Waiting for connections to close. (CTRL+C to force quit)"
            logger.info(msg)
            while self.server_state.connections and not self.force_exit:
                await asyncio.sleep(0.1)

        # Wait for existing tasks to complete.
        if self.server_state.tasks and not self.force_exit:
            msg = "Waiting for background tasks to complete. (CTRL+C to force quit)"
            logger.info(msg)
            while self.server_state.tasks and not self.force_exit:
                await asyncio.sleep(0.1)

        for server in self.servers:
            await server.wait_closed()

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        # Signals can only be listened to from the main thread.
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        # always use signal.signal, even if loop.add_signal_handler is available
        # this allows to restore previous signal handlers later on
        original_handlers = {sig: signal.signal(sig, self.handle_exit) for sig in HANDLED_SIGNALS}
        try:
            yield
        finally:
            for sig, handler in original_handlers.items():
                signal.signal(sig, handler)
        # If we did gracefully shut down due to a signal, try to
        # trigger the expected behaviour now; multiple signals would be
        # done LIFO, see https://stackoverflow.com/questions/48434964
        for captured_signal in reversed(self._captured_signals):
            signal.raise_signal(captured_signal)

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._captured_signals.append(sig)
        if self.should_exit and sig == signal.SIGINT:
            self.force_exit = True  # pragma: full coverage
        else:
            self.should_exit = True

        # Phase 2a: Trigger shutdown event to wake main_loop immediately
        if hasattr(self, 'server_state') and hasattr(self.server_state, '_shutdown_event'):
            self.server_state._shutdown_event.set()
