"""Regression tests for the /add_workers endpoint guards (CWE-918)."""

import asyncio
import unittest
from unittest import mock

from launch_server import (
    MAX_WORKER_URLS_PER_REQUEST,
    is_router_self_reference,
    normalize_worker_url,
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


class TestBatchLimit(unittest.TestCase):
    def test_limit_is_bounded(self):
        self.assertGreater(MAX_WORKER_URLS_PER_REQUEST, 0)
        self.assertLessEqual(MAX_WORKER_URLS_PER_REQUEST, 1000)

    def test_batch_limit_is_enforced_by_the_endpoint(self):
        """The endpoint must reject a batch that is larger than the limit.

        Asserting the constant alone does not prove that the limit is
        enforced, therefore the endpoint itself is called.
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
        self.assertEqual(resp.status_code, 400)

        allowed = [f"http://worker{i}:8001" for i in range(MAX_WORKER_URLS_PER_REQUEST)]
        resp = client.post("/add_workers", json={"urls": allowed})
        self.assertNotEqual(resp.status_code, 400)


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
