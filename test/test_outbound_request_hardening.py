"""Regression tests for the outbound request hardening (CWE-918)."""

import asyncio
import unittest
from unittest import mock

from aiohttp import web

from launch_server import (
    MAX_HEALTH_REDIRECTS,
    Router,
    _is_internal_address,
    _is_safe_outbound_url,
)


def _run(coro):
    return asyncio.run(coro)


class TestIsInternalAddress(unittest.TestCase):
    def test_internal_ranges(self):
        for address in [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "::1",
            "fc00::1",
            "fe80::1",
            "0.0.0.0",
        ]:
            with self.subTest(address=address):
                self.assertTrue(_is_internal_address(address))

    def test_ipv4_mapped_ipv6(self):
        self.assertTrue(_is_internal_address("::ffff:127.0.0.1"))
        self.assertTrue(_is_internal_address("::ffff:10.0.0.1"))

    def test_public_addresses(self):
        for address in ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"]:
            with self.subTest(address=address):
                self.assertFalse(_is_internal_address(address))

    def test_garbage(self):
        self.assertFalse(_is_internal_address("not-an-ip"))


class TestIsSafeOutboundUrl(unittest.TestCase):
    def test_rejects_internal_target(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["169.254.169.254"]):
            self.assertFalse(_run(_is_safe_outbound_url("http://metadata.example.com/x")))

    def test_rejects_internal_loopback(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["127.0.0.1"]):
            self.assertFalse(_run(_is_safe_outbound_url("http://localhost:8001/health")))

    def test_accepts_public_target(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["93.184.216.34"]):
            self.assertTrue(_run(_is_safe_outbound_url("http://worker.example.com/health")))

    def test_rejects_non_http_scheme(self):
        self.assertFalse(_run(_is_safe_outbound_url("file:///etc/passwd")))

    def test_unresolvable_is_left_to_the_request(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=[]):
            self.assertTrue(_run(_is_safe_outbound_url("http://unknown.example.com/health")))


class TestHealthRequestRedirects(unittest.TestCase):
    """The health check must not follow a redirect into the internal network."""

    @staticmethod
    def _start_server(handlers):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = web.Application()
        for path, handler in handlers.items():
            app.router.add_route("*", path, handler)
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        port = runner.addresses[0][1]
        return loop, runner, port

    def test_redirect_to_internal_target_is_blocked(self):
        async def redirector(request):
            raise web.HTTPFound("http://169.254.169.254/latest/meta-data")

        loop, runner, port = self._start_server({"/health": redirector})
        try:

            async def scenario():
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    with mock.patch("launch_server._resolve_addresses_sync", return_value=["169.254.169.254"]):
                        return await Router._health_request(session, f"http://127.0.0.1:{port}/health")

            status = loop.run_until_complete(scenario())
            self.assertEqual(status, -1)
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()

    def test_redirect_to_public_target_is_followed(self):
        async def redirector(request):
            raise web.HTTPFound("/final")

        async def final(request):
            return web.Response(status=200, text="ok")

        loop, runner, port = self._start_server({"/health": redirector, "/final": final})
        try:

            async def scenario():
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    with mock.patch("launch_server._resolve_addresses_sync", return_value=["93.184.216.34"]):
                        return await Router._health_request(session, f"http://127.0.0.1:{port}/health")

            status = loop.run_until_complete(scenario())
            self.assertEqual(status, 200)
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()

    def test_redirect_hop_limit(self):
        async def redirector(request):
            raise web.HTTPFound("/health")

        loop, runner, port = self._start_server({"/health": redirector})
        try:

            async def scenario():
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    with mock.patch("launch_server._resolve_addresses_sync", return_value=["93.184.216.34"]):
                        return await Router._health_request(session, f"http://127.0.0.1:{port}/health")

            status = loop.run_until_complete(scenario())
            self.assertEqual(status, -1)
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()


class TestWaitForHealthyWorker(unittest.TestCase):
    def test_does_not_block_the_event_loop(self):
        """The retry interval must not block the event loop."""

        async def scenario():
            with mock.patch("launch_server._resolve_addresses_sync", return_value=["93.184.216.34"]):
                with mock.patch.object(Router, "_health_request", return_value=503):
                    task = asyncio.create_task(Router.wait_for_healthy_worker("http://worker:8001", 2, 1))
                    await asyncio.sleep(0.2)
                    # The event loop is still responsive while the health check
                    # waits for the next attempt.
                    self.assertFalse(task.done())
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        _run(scenario())

    def test_redirect_limit_is_bounded(self):
        self.assertGreater(MAX_HEALTH_REDIRECTS, 0)
        self.assertLessEqual(MAX_HEALTH_REDIRECTS, 10)


if __name__ == "__main__":
    unittest.main()
