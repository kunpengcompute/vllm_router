"""Regression tests for the management plane authentication (CWE-862).

The authentication is opt in: as long as no token is configured the
management endpoints behave exactly like they did before. When a token is
configured, every request that does not carry it is rejected.
"""

import asyncio
import os
import threading
import unittest
from unittest import mock

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from launch_server import create_app


class _StubRouter:
    """Minimal stand-in for launch_server.Router (no worker needed in tests)."""

    router = None

    async def get_worker_urls(self):
        return ["http://worker1:8001"]

    async def forward_request(self, body, api):
        return JSONResponse({"ok": True})

    async def add_workers(self, urls):
        return [], [], "add server succeed."

    def remove_workers(self, urls):
        return "delete server succeed."


def _app(**kwargs):
    defaults = {
        "worker_urls": [],
        "policy_config": None,
        "admin_token": "",
        "api_token": "",
    }
    defaults.update(kwargs)
    app = create_app(**defaults)
    # The lifespan requires at least one healthy worker, which is not available
    # in the unit test environment, so the router is stubbed out here.
    # The ASGI middleware stack has to be built *before* app.router is
    # replaced, otherwise it would capture the stub instead of the real
    # APIRouter (the same ordering the production lifespan relies on).
    app.middleware_stack = app.build_middleware_stack()
    app.router = _StubRouter()
    return app


MANAGEMENT_ENDPOINTS = [
    ("post", "/add_workers", {"urls": ["http://worker2:8001"]}),
    ("post", "/remove_workers", {"urls": ["http://worker1:8001"]}),
    ("get", "/list_workers", None),
    ("get", "/pretty_print_tree", None),
]


class TestAuthenticationIsOptIn(unittest.TestCase):
    """Without a token nothing changes, the endpoints stay reachable."""

    def setUp(self):
        self.client = TestClient(_app())

    def test_the_management_endpoints_are_reachable_without_a_token(self):
        for method, path, payload in MANAGEMENT_ENDPOINTS:
            with self.subTest(endpoint=path):
                if method == "post":
                    response = self.client.post(path, json=payload)
                else:
                    response = self.client.get(path)
                self.assertNotEqual(response.status_code, 401)
                self.assertNotEqual(response.status_code, 403)

    def test_the_health_endpoint_stays_public(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

    def test_the_data_plane_is_reachable_without_a_token(self):
        response = self.client.post("/v1/completions", json={"prompt": "hello"})
        self.assertNotEqual(response.status_code, 401)


class TestManagementAuth(unittest.TestCase):
    """With a token the management endpoints require it."""

    def setUp(self):
        self.client = TestClient(_app(admin_token="s3cr3t-admin-token-value"))

    def test_management_endpoints_reject_anonymous(self):
        for method, path, payload in MANAGEMENT_ENDPOINTS:
            with self.subTest(endpoint=path):
                if method == "post":
                    response = self.client.post(path, json=payload)
                else:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 401)

    def test_management_endpoints_accept_valid_bearer_token(self):
        headers = {"Authorization": "Bearer s3cr3t-admin-token-value"}
        for method, path, payload in MANAGEMENT_ENDPOINTS:
            with self.subTest(endpoint=path):
                if method == "post":
                    response = self.client.post(path, json=payload, headers=headers)
                else:
                    response = self.client.get(path, headers=headers)
                self.assertNotEqual(response.status_code, 401)

    def test_management_endpoints_accept_x_admin_token(self):
        headers = {"X-Admin-Token": "s3cr3t-admin-token-value"}
        for method, path, payload in MANAGEMENT_ENDPOINTS:
            with self.subTest(endpoint=path):
                if method == "post":
                    response = self.client.post(path, json=payload, headers=headers)
                else:
                    response = self.client.get(path, headers=headers)
                self.assertNotEqual(response.status_code, 401)

    def test_management_endpoints_reject_wrong_token(self):
        headers = {"Authorization": "Bearer wrong-token"}
        for method, path, payload in MANAGEMENT_ENDPOINTS:
            with self.subTest(endpoint=path):
                if method == "post":
                    response = self.client.post(path, json=payload, headers=headers)
                else:
                    response = self.client.get(path, headers=headers)
                self.assertEqual(response.status_code, 401)

    def test_the_health_stays_public(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

    def test_the_data_plane_token_is_optional(self):
        client = TestClient(_app(admin_token="s3cr3t-admin-token-value"))
        response = client.post("/v1/completions", json={"prompt": "hello"})
        self.assertNotEqual(response.status_code, 401)

    def test_the_data_plane_rejects_a_wrong_token(self):
        client = TestClient(_app(admin_token="s3cr3t-admin-token-value", api_token="s3cr3t-api-token-value"))
        response = client.post("/v1/completions", json={"prompt": "hello"})
        self.assertEqual(response.status_code, 401)

    def test_the_data_plane_accepts_the_correct_token(self):
        client = TestClient(_app(admin_token="s3cr3t-admin-token-value", api_token="s3cr3t-api-token-value"))
        response = client.post(
            "/v1/completions",
            json={"prompt": "hello"},
            headers={"Authorization": "Bearer s3cr3t-api-token-value"},
        )
        self.assertNotEqual(response.status_code, 401)


class TestNonAsciiToken(unittest.TestCase):
    """A token that is not ASCII must not turn into an internal error.

    ``hmac.compare_digest`` refuses ``str`` objects with non ASCII characters
    and raises a ``TypeError``, which would have answered a wrong token with
    a 500 instead of a 401.
    """

    def test_a_non_ascii_token_matches_itself(self):
        from utils.auth import token_matches

        token = "密-token-令牌-🔑"
        self.assertTrue(token_matches(token, token))

    def test_a_non_ascii_token_does_not_match_another_one(self):
        from utils.auth import token_matches

        self.assertFalse(token_matches("密-token-令牌-🔑", "密-token-令牌-🔒"))
        self.assertFalse(token_matches("密-token-令牌-🔑", ""))

    def test_a_non_ascii_configured_token_does_not_produce_a_server_error(self):
        """A configured token that is not ASCII must not raise a TypeError.

        The header of a request is latin-1, so the token that the client
        sends is always ASCII here. The configured token is not, and the
        comparison of the two used to raise a TypeError, which answered the
        request with a 500 instead of a 401.
        """
        client = TestClient(_app(admin_token="密-token-令牌"))
        response = client.get("/list_workers", headers={"Authorization": "Bearer wrong-token"})
        self.assertEqual(response.status_code, 401)

    def test_a_non_ascii_configured_token_does_not_produce_a_server_error_on_the_data_plane(self):
        client = TestClient(_app(api_token="密-token-令牌"))
        response = client.post("/v1/completions", json={"prompt": "hello"})
        self.assertEqual(response.status_code, 401)


class TestTokenSource(unittest.TestCase):
    def test_the_environment_variable_is_used_when_no_option_is_given(self):
        from utils.auth import ADMIN_TOKEN_ENV, resolve_token

        with mock.patch.dict(os.environ, {ADMIN_TOKEN_ENV: "from-the-environment"}):
            self.assertEqual(resolve_token("", ADMIN_TOKEN_ENV), "from-the-environment")

    def test_the_command_line_wins_over_the_environment_variable(self):
        from utils.auth import ADMIN_TOKEN_ENV, resolve_token

        with mock.patch.dict(os.environ, {ADMIN_TOKEN_ENV: "from-the-environment"}):
            self.assertEqual(resolve_token("from-the-command-line", ADMIN_TOKEN_ENV), "from-the-command-line")

    def test_without_a_token_the_protection_is_off(self):
        from utils.auth import ADMIN_TOKEN_ENV, resolve_token

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_token("", ADMIN_TOKEN_ENV), "")


class TestManagementEndpointsDoNotCrash(unittest.TestCase):
    """The removal of a worker must never end in an internal server error."""

    def test_remove_worker_without_a_policy_router(self):
        """The policy router only exists when the router was started with one."""
        from launch_server import Router

        async def scenario():
            router = Router()
            router.backends["http://worker:8001"] = object()
            msg = router.remove_workers(["http://worker:8001"])
            await router.session.close()
            return msg

        msg = asyncio.run(scenario())
        self.assertIn("succeed", msg)

    def test_remove_unknown_worker_is_reported_not_raised(self):
        from launch_server import Router

        async def scenario():
            router = Router()
            msg = router.remove_workers(["http://unknown:8001"])
            await router.session.close()
            return msg

        msg = asyncio.run(scenario())
        self.assertIn("failed", msg)

    def test_repeated_removal_does_not_raise(self):
        """The worker may already be gone, ``list.remove`` would raise."""
        from src.router import RoundRobinRouter, RouteSelector

        router = RoundRobinRouter(
            worker_urls=["http://worker:8001"],
            current_index=0,
            timeout_secs=300,
            interval_secs=10,
        )
        selector = RouteSelector()

        selector.remove_worker("http://worker:8001", router)
        # The second removal must not raise a ValueError.
        selector.remove_worker("http://worker:8001", router)

        self.assertNotIn("http://worker:8001", router.worker_urls)

    def test_concurrent_removal_does_not_raise(self):
        """The health monitor and the endpoint can remove the same worker."""
        from src.router import RoundRobinRouter, RouteSelector

        router = RoundRobinRouter(
            worker_urls=["http://worker:8001"],
            current_index=0,
            timeout_secs=300,
            interval_secs=10,
        )
        selector = RouteSelector()
        errors = []

        def remove():
            try:
                selector.remove_worker("http://worker:8001", router)
            except Exception as exc:  # noqa: BLE001 - the error is the result
                errors.append(exc)

        threads = [threading.Thread(target=remove) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])

    def test_removing_an_unknown_tenant_does_not_crash_the_tree(self):
        """A tenant that was never inserted must not raise a KeyError."""
        from src.tree import Tree

        tree = Tree()
        tree.remove_tenant("never-inserted")
        self.assertEqual(tree.get_tenant_char_count().get("never-inserted", 0), 0)

    def test_the_queue_of_a_removed_worker_is_cleaned_up(self):
        from src.router import CacheAwareRouter
        from src.tree import Tree

        tree = Tree()
        router = CacheAwareRouter(
            worker_urls=["http://worker:8001"],
            tree=tree,
            running_queue={"http://worker:8001": 3},
            processed_queue={"http://worker:8001": 7},
            cache_threshold=0.5,
            balance_abs_threshold=32,
            balance_rel_threshold=1.0001,
            timeout_secs=300,
            interval_secs=10,
        )

        router.delete_worker("http://worker:8001")
        self.assertNotIn("http://worker:8001", router.running_queue)
        self.assertNotIn("http://worker:8001", router.processed_queue)

    def test_deleting_an_unknown_worker_does_not_raise(self):
        from src.router import CacheAwareRouter
        from src.tree import Tree

        tree = Tree()
        router = CacheAwareRouter(
            worker_urls=[],
            tree=tree,
            running_queue={},
            processed_queue={},
            cache_threshold=0.5,
            balance_abs_threshold=32,
            balance_rel_threshold=1.0001,
            timeout_secs=300,
            interval_secs=10,
        )

        router.delete_worker("http://unknown:8001")


if __name__ == "__main__":
    unittest.main()
