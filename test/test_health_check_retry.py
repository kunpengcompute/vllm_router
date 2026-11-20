"""Regression tests for the retry behaviour of the health check.

The health check used to close its session inside the retry loop, so every
attempt after the first one failed with "Session is closed" and a worker that
came up late was never registered. It also used a blocking sleep, which
stopped the whole event loop while it waited for that worker.
"""

import asyncio
import unittest
from unittest import mock

import aiohttp
from aiohttp.client_reqrep import ConnectionKey

from launch_server import Router


def _connection_error():
    """A connection error of the kind that aiohttp raises."""
    key = ConnectionKey(
        host="worker",
        port=8001,
        is_ssl=False,
        ssl=None,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )
    return aiohttp.client_exceptions.ClientConnectorError(key, OSError("Connection refused"))


class _FakeResponse:
    """The part of a response that the health check uses."""

    def __init__(self, status=200, session=None):
        self.status = status
        self._session = session

    async def __aenter__(self):
        if self._session is not None and self._session.closed:
            # This is what aiohttp does when the session was closed: the
            # request context manager raises as soon as it is entered.
            raise RuntimeError("Session is closed")
        return self

    async def __aexit__(self, *args):
        return False


def _fake_get(statuses):
    """Return a fake ``ClientSession.get`` and the list of the called URLs.

    The fake answers with the given statuses, the last one is repeated. An
    entry that is an exception is raised instead of being answered.

    The fake reproduces the behaviour of a closed session, otherwise the
    test would not notice that the session was closed inside the retry
    loop.
    """
    calls = []

    def get(self, url, *args, **kwargs):
        calls.append(url)
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        if isinstance(status, Exception):
            raise status
        return _FakeResponse(status, session=self)

    return get, calls


class TestHealthCheckRetry(unittest.TestCase):
    def test_the_second_health_check_succeeds(self):
        """The first attempt failed, the second one has to be able to succeed.

        The session was closed inside the retry loop, so every attempt after
        the first one failed with "Session is closed".
        """

        async def scenario():
            get, calls = _fake_get([500, 200])
            with mock.patch.object(aiohttp.ClientSession, "get", get):
                healthy = await Router.wait_for_healthy_worker("http://worker:8001", 5, 0)
            return healthy, calls

        healthy, calls = asyncio.run(scenario())
        self.assertTrue(healthy)
        self.assertEqual(len(calls), 2)

    def test_the_health_check_gives_up_after_the_timeout(self):
        async def scenario():
            get, calls = _fake_get([500])
            with mock.patch.object(aiohttp.ClientSession, "get", get):
                healthy = await Router.wait_for_healthy_worker("http://worker:8001", 0.2, 0)
            return healthy, calls

        healthy, calls = asyncio.run(scenario())
        self.assertFalse(healthy)
        self.assertGreater(len(calls), 1)

    def test_a_connection_error_does_not_kill_the_retry_loop(self):
        async def scenario():
            get, calls = _fake_get([_connection_error(), 200])
            with mock.patch.object(aiohttp.ClientSession, "get", get):
                healthy = await Router.wait_for_healthy_worker("http://worker:8001", 5, 0)
            return healthy, calls

        healthy, calls = asyncio.run(scenario())
        self.assertTrue(healthy)
        self.assertEqual(len(calls), 2)

    def test_the_event_loop_is_not_blocked_while_the_router_waits(self):
        """A blocking sleep stopped every other task of the router."""

        async def scenario():
            ticks = []

            async def ticker():
                while True:
                    await asyncio.sleep(0.01)
                    ticks.append(1)

            get, _ = _fake_get([500])
            with mock.patch.object(aiohttp.ClientSession, "get", get):
                task = asyncio.create_task(ticker())
                try:
                    await Router.wait_for_healthy_worker("http://worker:8001", 0.3, 0.05)
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            return len(ticks)

        ticks = asyncio.run(scenario())
        # With a blocking sleep the ticker would have run only once, when the
        # event loop got the control back after the whole wait.
        self.assertGreater(ticks, 5)


class TestHealthCheckSessionLifecycle(unittest.TestCase):
    """The session of the health check must not leak."""

    @staticmethod
    def _run(statuses, timeout_secs):
        created = []
        real_session = aiohttp.ClientSession

        def session_factory(*args, **kwargs):
            session = real_session(*args, **kwargs)
            created.append(session)
            return session

        async def scenario():
            get, _ = _fake_get(statuses)
            with mock.patch.object(aiohttp.ClientSession, "get", get):
                with mock.patch.object(aiohttp, "ClientSession", session_factory):
                    healthy = await Router.wait_for_healthy_worker("http://worker:8001", timeout_secs, 0)
            return healthy, created

        return asyncio.run(scenario())

    def test_the_session_is_closed_after_a_success(self):
        healthy, created = self._run([200], 5)
        self.assertTrue(healthy)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    def test_the_session_is_closed_after_a_failure(self):
        healthy, created = self._run([500], 0.2)
        self.assertFalse(healthy)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    def test_the_session_is_closed_after_a_timeout(self):
        """The timeout path returns without closing the session in the master."""
        healthy, created = self._run([500], 0.1)
        self.assertFalse(healthy)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    def test_the_session_is_closed_after_a_connection_error(self):
        created = []
        real_session = aiohttp.ClientSession

        def session_factory(*args, **kwargs):
            session = real_session(*args, **kwargs)
            created.append(session)
            return session

        async def scenario():
            get, _ = _fake_get([_connection_error()])
            with mock.patch.object(aiohttp.ClientSession, "get", get):
                with mock.patch.object(aiohttp, "ClientSession", session_factory):
                    await Router.wait_for_healthy_worker("http://worker:8001", 0.2, 0)

        asyncio.run(scenario())
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)


if __name__ == "__main__":
    unittest.main()
