"""Regression tests for the /add_workers endpoint guards (CWE-918)."""

import asyncio
import os
import unittest
from unittest import mock

from launch_server import (
    MAX_WORKER_URLS_PER_REQUEST,
    MAX_WORKER_URLS_PER_REQUEST_ENV,
    is_router_self_reference,
    normalize_worker_url,
    normalize_worker_urls,
    parse_endpoint,
)


def _run(coro):
    return asyncio.run(coro)


class TestNormalizeWorkerUrl(unittest.TestCase):
    def test_strips_trailing_slash(self):
        self.assertEqual(normalize_worker_url("http://worker:8001/"), "http://worker:8001")
        self.assertEqual(normalize_worker_url("http://worker:8001"), "http://worker:8001")

    def test_strips_whitespace(self):
        self.assertEqual(normalize_worker_url("  http://worker:8001  "), "http://worker:8001")

    def test_keeps_path(self):
        self.assertEqual(normalize_worker_url("http://worker:8001/inference/"), "http://worker:8001/inference")


class TestNormalizeWorkerUrls(unittest.TestCase):
    def test_deduplicates_and_keeps_the_order(self):
        self.assertEqual(
            normalize_worker_urls(["http://b:8001/", "http://a:8001", "http://b:8001"]),
            ["http://b:8001", "http://a:8001"],
        )

    def test_drops_empty_entries(self):
        self.assertEqual(normalize_worker_urls(["", "http://a:8001", "   "]), ["http://a:8001"])


class TestParseEndpoint(unittest.TestCase):
    def test_parses_a_regular_url(self):
        self.assertEqual(parse_endpoint("http://worker:8001"), ("worker", 8001))

    def test_defaults_to_the_scheme_port(self):
        self.assertEqual(parse_endpoint("http://worker"), ("worker", 80))
        self.assertEqual(parse_endpoint("https://worker"), ("worker", 443))

    def test_non_numeric_port_is_rejected(self):
        self.assertIsNone(parse_endpoint("http://worker:bad"))

    def test_out_of_range_port_is_rejected(self):
        self.assertIsNone(parse_endpoint("http://worker:99999"))

    def test_unterminated_ipv6_literal_is_rejected(self):
        self.assertIsNone(parse_endpoint("http://[::1:8001"))


class TestIsRouterSelfReference(unittest.TestCase):
    def test_detects_loopback_on_the_router_port(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["127.0.0.1"]):
            self.assertTrue(_run(is_router_self_reference("http://127.0.0.1:8008", 8008)))

    def test_detects_local_interface_address(self):
        with (
            mock.patch("launch_server._resolve_addresses_sync", return_value=["192.168.1.10"]),
            mock.patch("launch_server._local_addresses", return_value={"192.168.1.10"}),
        ):
            self.assertTrue(_run(is_router_self_reference("http://192.168.1.10:8008", 8008)))

    def test_other_port_is_not_a_self_reference(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["127.0.0.1"]):
            self.assertFalse(_run(is_router_self_reference("http://127.0.0.1:8001", 8008)))

    def test_other_host_is_not_a_self_reference(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["93.184.216.34"]):
            self.assertFalse(_run(is_router_self_reference("http://93.184.216.34:8008", 8008)))

    def test_unknown_router_port_disables_the_check(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["127.0.0.1"]):
            self.assertFalse(_run(is_router_self_reference("http://127.0.0.1:8008", 0)))

    def test_unresolvable_host_is_not_a_self_reference(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=[]):
            self.assertFalse(_run(is_router_self_reference("http://unknown.example.com:8008", 8008)))

    def test_non_numeric_port_does_not_raise(self):
        """A malformed port must be rejected, it must not raise.

        ``parsed.port`` raises a ValueError for a non numeric port. The
        property was read outside of the try block, so the registration
        endpoint answered with an internal server error.
        """
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["127.0.0.1"]):
            self.assertFalse(_run(is_router_self_reference("http://worker:bad", 8008)))

    def test_out_of_range_port_does_not_raise(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["127.0.0.1"]):
            self.assertFalse(_run(is_router_self_reference("http://worker:99999", 8008)))

    def test_unterminated_ipv6_literal_does_not_raise(self):
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["127.0.0.1"]):
            self.assertFalse(_run(is_router_self_reference("http://[::1:8001", 8008)))

    def test_detects_the_unspecified_address(self):
        """``0.0.0.0`` reaches every local socket, including the router."""
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["0.0.0.0"]):
            self.assertTrue(_run(is_router_self_reference("http://0.0.0.0:8008", 8008)))

    def test_detects_the_ipv4_mapped_loopback(self):
        """``::ffff:127.0.0.1`` is the IPv4 loopback in its IPv6 notation."""
        with mock.patch("launch_server._resolve_addresses_sync", return_value=["::ffff:127.0.0.1"]):
            self.assertTrue(_run(is_router_self_reference("http://[::ffff:127.0.0.1]:8008", 8008)))


class TestBatchLimit(unittest.TestCase):
    def test_default_limit_matches_the_limit_of_the_request_model(self):
        """The default must not change the behaviour of an existing deployment.

        The request model accepts at most 1000 URLs per request, a lower
        default would be an API compatibility change.
        """
        self.assertEqual(MAX_WORKER_URLS_PER_REQUEST, 1000)

    def test_limit_is_configurable_by_environment(self):
        from launch_server import _env_int

        with mock.patch.dict(os.environ, {MAX_WORKER_URLS_PER_REQUEST_ENV: "16"}):
            self.assertEqual(_env_int(MAX_WORKER_URLS_PER_REQUEST_ENV, MAX_WORKER_URLS_PER_REQUEST), 16)

    def test_invalid_environment_value_falls_back_to_the_default(self):
        from launch_server import _env_int

        with mock.patch.dict(os.environ, {MAX_WORKER_URLS_PER_REQUEST_ENV: "not-a-number"}):
            self.assertEqual(_env_int(MAX_WORKER_URLS_PER_REQUEST_ENV, MAX_WORKER_URLS_PER_REQUEST), 1000)

        with mock.patch.dict(os.environ, {MAX_WORKER_URLS_PER_REQUEST_ENV: "0"}):
            self.assertEqual(_env_int(MAX_WORKER_URLS_PER_REQUEST_ENV, MAX_WORKER_URLS_PER_REQUEST), 1000)

    def test_batch_limit_is_enforced_by_the_endpoint(self):
        """The endpoint must reject a batch that is larger than the limit.

        Asserting the constant alone does not prove that the limit is
        enforced, therefore the endpoint itself is called. The limit is
        configurable, the test therefore configures a small one.
        """
        from fastapi.testclient import TestClient

        from launch_server import create_app

        app = create_app([], None, router_port=8008)
        app.middleware_stack = app.build_middleware_stack()

        class Stub:
            router = None

            async def add_workers(self, urls):
                return [], [], "ok"

        app.router = Stub()
        client = TestClient(app, raise_server_exceptions=False)

        with mock.patch.dict(os.environ, {MAX_WORKER_URLS_PER_REQUEST_ENV: "3"}):
            resp = client.post(
                "/add_workers",
                json={"urls": ["http://a:8001", "http://b:8001", "http://c:8001", "http://d:8001"]},
            )
            self.assertEqual(resp.status_code, 400)

            resp = client.post(
                "/add_workers",
                json={"urls": ["http://a:8001", "http://b:8001", "http://c:8001"]},
            )
            self.assertNotEqual(resp.status_code, 400)

    def test_the_default_limit_matches_the_limit_of_the_request_model(self):
        """A batch above the default is rejected by the request model.

        The default of the endpoint is the limit that WorkUrls already
        enforced, so an existing deployment does not see a new error.
        """
        from fastapi.testclient import TestClient

        from launch_server import create_app

        app = create_app([], None, router_port=8008)
        app.middleware_stack = app.build_middleware_stack()

        class Stub:
            router = None

            async def add_workers(self, urls):
                return [], [], "ok"

        app.router = Stub()
        client = TestClient(app, raise_server_exceptions=False)

        too_many = [f"http://worker{i}:8001" for i in range(MAX_WORKER_URLS_PER_REQUEST + 1)]
        resp = client.post("/add_workers", json={"urls": too_many})
        # 422: the request model rejected the batch, the endpoint was not
        # reached. The behaviour is the one of the master branch.
        self.assertEqual(resp.status_code, 422)

        allowed = [f"http://worker{i}:8001" for i in range(MAX_WORKER_URLS_PER_REQUEST)]
        resp = client.post("/add_workers", json={"urls": allowed})
        self.assertNotEqual(resp.status_code, 400)

    def test_batch_limit_follows_the_environment(self):
        from fastapi.testclient import TestClient

        from launch_server import create_app

        app = create_app([], None, router_port=8008)
        app.middleware_stack = app.build_middleware_stack()

        class Stub:
            router = None

            async def add_workers(self, urls):
                return [], [], "ok"

        app.router = Stub()
        client = TestClient(app, raise_server_exceptions=False)

        with mock.patch.dict(os.environ, {MAX_WORKER_URLS_PER_REQUEST_ENV: "2"}):
            resp = client.post("/add_workers", json={"urls": ["http://a:8001", "http://b:8001", "http://c:8001"]})
        self.assertEqual(resp.status_code, 400)


class TestMalformedPortDoesNotBreakTheEndpoint(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        from launch_server import create_app

        app = create_app([], None, router_port=8008)
        app.middleware_stack = app.build_middleware_stack()

        class Stub:
            router = None

            async def add_workers(self, urls):
                return [], [], "ok"

        app.router = Stub()
        return TestClient(app, raise_server_exceptions=False)

    def test_non_numeric_port_is_not_an_internal_server_error(self):
        client = self._client()
        resp = client.post("/add_workers", json={"urls": ["http://worker:bad"]})
        self.assertNotEqual(resp.status_code, 500)

    def test_out_of_range_port_is_not_an_internal_server_error(self):
        client = self._client()
        resp = client.post("/add_workers", json={"urls": ["http://worker:99999"]})
        self.assertNotEqual(resp.status_code, 500)


class TestRemovalUsesTheSameNormalisation(unittest.TestCase):
    """Registering and removing a worker must agree on the spelling.

    The registration path normalised the URL, the removal path matched the
    raw string. A worker that was registered as ``http://worker:8001/`` was
    therefore never removed.
    """

    @staticmethod
    def _remove(spelling: str, remove_spelling: str):
        """Register a worker under ``spelling`` and remove it under ``remove_spelling``.

        The router owns an aiohttp session, therefore it has to be created
        inside a running event loop.
        """

        class FakePolicyRouter:
            """A minimal policy router.

            The removal path removes the worker from the policy router, a
            router that was never initialised has none.
            """

            def __init__(self, worker_urls):
                self.worker_urls = list(worker_urls)

        # The worker pool is keyed by the normalised URL, exactly like the
        # registration path does it.
        key = normalize_worker_url(spelling)

        async def scenario():
            from launch_server import Router

            router = Router()
            router.router = FakePolicyRouter([key])
            router.backends[key] = object()
            msg = router.remove_workers([remove_spelling])
            await router.session.close()
            return msg, dict(router.backends)

        return asyncio.run(scenario())

    def test_remove_with_the_same_spelling_succeeds(self):
        """The worker was registered as "http://worker:8001/"."""
        msg, backends = self._remove("http://worker:8001", "http://worker:8001/")
        self.assertIn("succeed", msg)
        self.assertNotIn("failed", msg)
        self.assertEqual(backends, {})

    def test_remove_with_the_short_spelling_succeeds(self):
        """The worker was registered without a trailing slash."""
        msg, backends = self._remove("http://worker:8001", "http://worker:8001")
        self.assertIn("succeed", msg)
        self.assertEqual(backends, {})

    def test_remove_uses_the_normalised_key(self):
        """The worker was registered with a trailing slash, removed without."""
        msg, backends = self._remove("http://worker:8001/", "http://worker:8001")
        self.assertIn("succeed", msg)
        self.assertEqual(backends, {})

    def test_remove_of_an_unknown_worker_still_fails(self):
        msg, backends = self._remove("http://worker:8001", "http://other:8001")
        self.assertIn("failed", msg)
        self.assertEqual(len(backends), 1)

    def test_the_endpoint_uses_the_same_normalisation(self):
        from fastapi.testclient import TestClient

        from launch_server import create_app

        app = create_app([], None, router_port=8008)
        app.middleware_stack = app.build_middleware_stack()

        class Stub:
            router = None

            def __init__(self):
                self.removed = []

            def remove_workers(self, urls):
                self.removed = list(urls)
                return "delete [] server succeed. "

        stub = Stub()
        app.router = stub
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/remove_workers", json={"urls": ["http://worker:8001/"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(stub.removed, ["http://worker:8001"])


class TestNormalizationIsAppliedOnEveryPath(unittest.TestCase):
    def test_the_router_normalizes_the_worker_url(self):
        """The command line path and the endpoint must agree.

        Normalising only in the endpoint left "http://worker:8001/" and
        "http://worker:8001" as two different workers in the pool.
        """
        from launch_server import Router

        async def scenario():
            router = Router()
            router.backends["http://worker:8001"] = object()

            async def unhealthy(urls, max_concurrency=10):
                return {url: False for url in urls}

            router.batch_check_url_health = unhealthy
            succeeded, failed, msg = await router.add_workers(["http://worker:8001/"])
            return succeeded, failed

        succeeded, failed = asyncio.run(scenario())
        # The trailing slash form is the same worker, so it is reported as
        # "already existing" and not as a new worker.
        self.assertEqual(succeeded, [])
        self.assertEqual(failed, ["http://worker:8001"])


if __name__ == "__main__":
    unittest.main()
