"""Regression tests for the management plane authentication (CWE-862)."""

import unittest

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from launch_server import create_app
from router.protocol import RouterArgs


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
        "admin_token": "s3cr3t-admin-token-value",
        "api_token": "",
        "allow_anonymous_admin": False,
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


class TestManagementAuth(unittest.TestCase):
    def setUp(self):
        self.app = _app()
        self.client = TestClient(self.app)

    def test_management_endpoints_reject_anonymous(self):
        for method, path in [
            ("post", "/add_workers"),
            ("post", "/remove_workers"),
            ("get", "/list_workers"),
            ("get", "/pretty_print_tree"),
        ]:
            with self.subTest(path=path):
                if method == "post":
                    resp = self.client.post(path, json={"urls": ["http://worker1:8001"]})
                else:
                    resp = self.client.get(path)
                self.assertIn(resp.status_code, (401, 403))

    def test_management_endpoints_accept_valid_bearer_token(self):
        headers = {"Authorization": "Bearer s3cr3t-admin-token-value"}
        resp = self.client.get("/list_workers", headers=headers)
        self.assertNotIn(resp.status_code, (401, 403))

    def test_management_endpoints_accept_x_admin_token(self):
        headers = {"X-Admin-Token": "s3cr3t-admin-token-value"}
        resp = self.client.get("/list_workers", headers=headers)
        self.assertNotIn(resp.status_code, (401, 403))

    def test_management_endpoints_reject_wrong_token(self):
        headers = {"Authorization": "Bearer wrong-token-value-here"}
        resp = self.client.get("/list_workers", headers=headers)
        self.assertEqual(resp.status_code, 401)

    def test_management_disabled_when_no_token_configured(self):
        app = _app(admin_token="")
        client = TestClient(app)
        resp = client.get("/list_workers")
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_admin_opt_in(self):
        app = _app(admin_token="", allow_anonymous_admin=True)
        client = TestClient(app)
        resp = client.get("/list_workers")
        self.assertNotIn(resp.status_code, (401, 403))

    def test_health_stays_public(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_data_plane_token_is_optional(self):
        resp = self.client.post("/v1/completions", json={"model": "m", "prompt": "hi"})
        # No token configured -> the request is not rejected by auth (it will
        # fail later because no worker is available).
        self.assertNotEqual(resp.status_code, 401)

    def test_data_plane_rejects_wrong_token(self):
        app = _app(api_token="s3cr3t-api-token-value")
        client = TestClient(app)
        resp = client.post(
            "/v1/completions",
            json={"model": "m", "prompt": "hi"},
            headers={"Authorization": "Bearer nope"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_data_plane_accepts_correct_token(self):
        app = _app(api_token="s3cr3t-api-token-value")
        client = TestClient(app)
        resp = client.post(
            "/v1/completions",
            json={"model": "m", "prompt": "hi"},
            headers={"Authorization": "Bearer s3cr3t-api-token-value"},
        )
        self.assertNotEqual(resp.status_code, 401)


class TestTokenStrength(unittest.TestCase):
    def test_short_token_rejected(self):
        with self.assertRaises(ValueError):
            RouterArgs(
                host="0.0.0.0",
                port=8008,
                worker_urls=["http://127.0.0.1:8001"],
                admin_token="short",
            )

    def test_long_token_accepted(self):
        args = RouterArgs(
            host="0.0.0.0",
            port=8008,
            worker_urls=["http://127.0.0.1:8001"],
            admin_token="0123456789abcdef0123",
        )
        self.assertEqual(args.admin_token, "0123456789abcdef0123")


if __name__ == "__main__":
    unittest.main()
