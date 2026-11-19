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


if __name__ == "__main__":
    unittest.main()
