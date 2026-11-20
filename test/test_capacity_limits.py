"""Regression tests for the resource limits of the router (CWE-400, CWE-770).

The limits of the tree, of the request path and of the worker pool used to
live in different layers and did not agree with each other. They are now
collected in a single :class:`CapacityConfig`, and the tests below make sure
that the layers really use the same values.
"""

import asyncio
import os
import time
import unittest
from unittest import mock

from src.tree import Tree
from utils.config import CapacityConfig


class TestCapacityConfig(unittest.TestCase):
    """The configuration is the single source of truth of the limits."""

    def test_the_defaults_are_the_documented_ones(self):
        config = CapacityConfig()
        self.assertEqual(config.max_tree_size, 2**24)
        self.assertEqual(config.max_tree_total_size, 2**24)
        self.assertEqual(config.max_tree_nodes, 2**24)
        self.assertEqual(config.max_cache_depth, 64)
        self.assertEqual(config.max_cache_text_length, 4096)
        self.assertEqual(config.max_workers, 256)

    def test_the_command_line_wins_over_the_environment(self):
        with mock.patch.dict(os.environ, {"VLLM_ROUTER_MAX_WORKERS": "10"}):
            config = CapacityConfig.from_namespace({"max_workers": 20})
            self.assertEqual(config.max_workers, 20)

    def test_the_environment_is_used_when_the_command_line_is_absent(self):
        with mock.patch.dict(os.environ, {"VLLM_ROUTER_MAX_WORKERS": "10"}):
            config = CapacityConfig.from_namespace({})
            self.assertEqual(config.max_workers, 10)

    def test_the_default_is_used_when_nothing_is_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = CapacityConfig.from_namespace({})
            self.assertEqual(config.max_workers, 256)

    def test_an_invalid_environment_value_falls_back_to_the_default(self):
        with mock.patch.dict(os.environ, {"VLLM_ROUTER_MAX_WORKERS": "not-a-number"}):
            config = CapacityConfig.from_namespace({})
            self.assertEqual(config.max_workers, 256)

    def test_zero_disables_a_limit(self):
        with mock.patch.dict(os.environ, {"VLLM_ROUTER_MAX_WORKERS": "0"}):
            config = CapacityConfig.from_namespace({})
            self.assertEqual(config.max_workers, 0)

    def test_the_tree_uses_the_limits_of_the_configuration(self):
        capacity = CapacityConfig(
            max_tree_nodes=128,
            max_cache_depth=8,
            max_cache_text_length=64,
            max_tree_total_size=256,
            max_tree_size=32,
        )
        tree = Tree(capacity=capacity)
        self.assertEqual(tree.max_nodes, 128)
        self.assertEqual(tree.max_depth, 8)
        self.assertEqual(tree.max_text_length, 64)
        self.assertEqual(tree.max_total_chars, 256)
        self.assertEqual(tree.max_tenant_chars, 32)


class TestNodeBudget(unittest.TestCase):
    """The node budget bounds the memory of the tree (CWE-400)."""

    def test_node_count_is_bounded(self):
        tree = Tree(max_nodes=64)
        for i in range(5000):
            # A shared prefix with a varying tail creates one node per request.
            tree.insert(f"{'A' * 64}{i:08d}-payload", "tenant1")
        self.assertLessEqual(tree.get_node_count(), 64)

    def test_the_tree_stays_usable_when_full(self):
        tree = Tree(max_nodes=16)
        for i in range(500):
            tree.insert(f"{'B' * 32}{i:08d}", "tenant1")
        # The tree is full, but a lookup must still work.
        matched_text, tenant = tree.prefix_match("B" * 32 + "00000001")
        self.assertTrue(matched_text.startswith("B"))
        self.assertEqual(tenant, "tenant1")

    def test_is_full_reports_the_budget(self):
        tree = Tree(max_nodes=8)
        self.assertFalse(tree.is_full())
        for i in range(200):
            tree.insert(f"{'C' * 16}{i:08d}", "tenant1")
        self.assertTrue(tree.is_full())

    def test_node_count_is_released_on_eviction(self):
        tree = Tree(max_nodes=1024)
        for i in range(200):
            tree.insert(f"{'D' * 16}{i:08d}", "tenant1")
        before = tree.get_node_count()
        tree.evict_tenant_by_size(1)
        after = tree.get_node_count()
        # The assertion has to be a real one: "after <= before" would also
        # hold when the eviction never releases a node at all.
        self.assertLess(after, before)
        self.assertGreaterEqual(after, 1)

    def test_the_tree_recovers_from_an_exhausted_node_budget(self):
        """A full tree must be able to accept a new entry again.

        Many tenants with a short prompt fill the node budget without any of
        them exceeding its own character quota, so the per tenant eviction
        does not touch them. Without a reclamation path the tree would stay
        full forever and would never cache another prompt.
        """
        tree = Tree(max_nodes=64)

        for tenant_index in range(20):
            tenant = f"tenant{tenant_index}"
            for i in range(10):
                tree.insert(f"{chr(ord('a') + tenant_index)}{i:04d}", tenant)

        self.assertTrue(tree.is_full())

        # The new prompt has to be accepted: the least recently used leaves
        # are evicted to make room.
        tree.insert("z-new-prompt", "tenant-new")

        self.assertLessEqual(tree.get_node_count(), 64)
        matched_text, tenant = tree.prefix_match("z-new-prompt")
        self.assertEqual(matched_text, "z-new-prompt")
        self.assertEqual(tenant, "tenant-new")

    def test_a_node_budget_of_zero_disables_the_limit(self):
        tree = Tree(max_nodes=0)
        for i in range(500):
            tree.insert(f"{'A' * 64}{i:08d}-payload", "tenant1")
        self.assertFalse(tree.is_full())
        self.assertGreater(tree.get_node_count(), 100)


class TestDepthLimit(unittest.TestCase):
    @staticmethod
    def _chain_depth(tree):
        node = tree.root
        depth = 0
        while node.children:
            node = next(iter(node.children.values()))
            depth += 1
        return depth

    @staticmethod
    def _build_chain(tree, length):
        """Progressively growing prompts build one node per character."""
        for i in range(1, length + 1):
            tree.insert("a" + "b" * i, "tenant1")

    def test_chain_length_is_bounded(self):
        tree = Tree(max_nodes=2**16, max_depth=8)
        self._build_chain(tree, 500)
        self.assertLessEqual(self._chain_depth(tree), 8)

    def test_prefix_match_is_bounded(self):
        tree = Tree(max_nodes=2**16, max_depth=8)
        self._build_chain(tree, 500)
        matched_text, tenant = tree.prefix_match("a" + "b" * 500)
        # The lookup visits at most max_depth nodes.
        self.assertLessEqual(len(matched_text), 9)

    def test_unbounded_tree_would_be_deep(self):
        # Sanity check: without the limit the same input builds a deep chain.
        tree = Tree(max_nodes=2**16, max_depth=2**16)
        self._build_chain(tree, 500)
        self.assertGreater(self._chain_depth(tree), 100)

    def test_a_depth_of_zero_disables_the_limit(self):
        tree = Tree(max_nodes=2**16, max_depth=0)
        self._build_chain(tree, 500)
        self.assertGreater(self._chain_depth(tree), 100)


class TestTextLengthLimit(unittest.TestCase):
    def test_text_is_truncated(self):
        tree = Tree(max_nodes=2**16, max_text_length=32)
        tree.insert("Z" * 4096, "tenant1")
        self.assertLessEqual(tree.get_used_size_per_tenant().get("tenant1", 0), 32)

    def test_short_text_is_untouched(self):
        tree = Tree(max_nodes=2**16, max_text_length=32)
        tree.insert("short", "tenant1")
        self.assertEqual(tree.get_used_size_per_tenant().get("tenant1"), 5)

    def test_a_text_length_of_zero_disables_the_truncation(self):
        tree = Tree(max_nodes=2**16, max_text_length=0)
        tree.insert("Z" * 4096, "tenant1")
        self.assertEqual(tree.get_used_size_per_tenant().get("tenant1"), 4096)


class TestRequestTextUsesTheSameLimit(unittest.TestCase):
    """The request path and the tree must agree on the limit.

    The request path used to truncate with a hard coded value while the tree
    used a configurable one, so the same prompt was cached completely or not
    at all depending on the path it took.
    """

    def test_the_request_text_is_truncated_to_the_configured_limit(self):
        async def scenario():
            from launch_server import Router

            router = Router(capacity=CapacityConfig(max_cache_text_length=4096))
            lengths = {}
            for length in (4095, 4096, 4097, 10000):
                body = {"prompt": "p" * length}
                lengths[length] = len(router.get_text_from_request(body, "v1/completions"))
            await router.session.close()
            return lengths

        lengths = asyncio.run(scenario())
        self.assertEqual(lengths[4095], 4095)
        self.assertEqual(lengths[4096], 4096)
        self.assertEqual(lengths[4097], 4096)
        self.assertEqual(lengths[10000], 4096)

    def test_the_request_text_and_the_tree_use_the_same_limit(self):
        async def scenario():
            from launch_server import Router

            capacity = CapacityConfig(max_cache_text_length=4096)
            router = Router(capacity=capacity)
            tree = Tree(capacity=capacity)

            text = router.get_text_from_request({"prompt": "p" * 10000}, "v1/completions")
            tree.insert(text, "tenant1")
            used = tree.get_used_size_per_tenant().get("tenant1", 0)
            await router.session.close()
            return len(text), used

        text_length, used = asyncio.run(scenario())
        self.assertEqual(text_length, 4096)
        self.assertEqual(used, 4096)

    def test_the_messages_of_a_request_are_truncated_as_well(self):
        async def scenario():
            from launch_server import Router

            router = Router(capacity=CapacityConfig(max_cache_text_length=64))
            body = {"messages": [{"role": "user", "content": "m" * 10000}]}
            text = router.get_text_from_request(body, "v1/completions")
            await router.session.close()
            return len(text)

        self.assertEqual(asyncio.run(scenario()), 64)

    def test_a_missing_prompt_is_empty(self):
        async def scenario():
            from launch_server import Router

            router = Router()
            empty = router.get_text_from_request({}, "v1/completions")
            empty_generate = router.get_text_from_request({}, "generate")
            await router.session.close()
            return empty, empty_generate

        empty, empty_generate = asyncio.run(scenario())
        self.assertEqual(empty, "")
        self.assertEqual(empty_generate, "")


class TestRoutingHitRate(unittest.TestCase):
    """The new limits must not destroy the cache hit rate of long prompts."""

    @staticmethod
    def _cache_aware_router(capacity):
        from src.router import CacheAwareRouter

        tree = Tree(capacity=capacity)
        worker_urls = ["http://a:8001", "http://b:8001"]
        for url in worker_urls:
            tree.insert("", url)
        return CacheAwareRouter(
            worker_urls=list(worker_urls),
            tree=tree,
            running_queue={url: 0 for url in worker_urls},
            processed_queue={url: 0 for url in worker_urls},
            cache_threshold=0.5,
            balance_abs_threshold=32,
            balance_rel_threshold=1.0001,
            timeout_secs=300,
            interval_secs=10,
        )

    def test_a_long_prompt_is_routed_to_the_same_worker(self):
        capacity = CapacityConfig()
        router = self._cache_aware_router(capacity)

        limit = capacity.max_cache_text_length
        shared = "X" * 5000
        # The request path truncates the prompt before it is cached.
        first = router.select_generate_worker(shared[:limit] + " one")
        second = router.select_generate_worker(shared[:limit] + " two")

        self.assertEqual(first, second)

    def test_the_hit_rate_of_a_long_prompt_is_based_on_the_stored_prefix(self):
        tree = Tree(capacity=CapacityConfig())
        tree.insert("P" * 4096, "worker1")

        matched_text, tenant = tree.prefix_match("P" * 4096 + " tail")
        self.assertEqual(tenant, "worker1")
        self.assertEqual(len(matched_text), 4096)
        # The match rate is the rate of the whole prompt, a long prompt that
        # shares its first 4096 characters still hits the cache.
        self.assertGreater(len(matched_text) / len("P" * 4096 + " tail"), 0.5)

    def test_the_hit_rate_of_a_prompt_below_the_limit_is_unchanged(self):
        tree = Tree(capacity=CapacityConfig())
        tree.insert("Q" * 100, "worker1")

        matched_text, tenant = tree.prefix_match("Q" * 100 + " tail")
        self.assertEqual(tenant, "worker1")
        self.assertEqual(len(matched_text), 100)


class TestGlobalBudget(unittest.TestCase):
    """The global budget bounds the memory of the whole tree (CWE-770)."""

    def test_the_global_budget_is_enforced_on_the_insert_path(self):
        """The budget must not depend on the periodic eviction thread.

        The eviction thread only runs every eviction_interval_secs, between
        two of its cycles the tree was free to grow far beyond its budget.
        """
        tree = Tree(capacity=CapacityConfig(max_tree_total_size=1000, max_tree_nodes=2**16))
        for i in range(50):
            tree.insert(f"{'x' * 50}{i:04d}", f"tenant{i % 5}")

        total = sum(tree.get_used_size_per_tenant().values())
        self.assertLessEqual(total, 1000)

    def test_the_per_tenant_quota_alone_is_not_a_limit(self):
        tree = Tree(capacity=CapacityConfig(max_tree_total_size=1000, max_tree_nodes=2**16))
        for tenant_index in range(20):
            tenant = f"tenant{tenant_index}"
            for i in range(10):
                tree.insert(f"{'x' * 50}{i:04d}", tenant)

        # Every tenant is far below its own quota, only the global budget
        # keeps the total in check.
        tree.evict_tenant_by_size(tree.max_tenant_chars)
        total = sum(tree.get_used_size_per_tenant().values())
        self.assertLessEqual(total, 1000)

    def test_the_global_budget_evicts_the_least_recently_used_entries(self):
        tree = Tree(capacity=CapacityConfig(max_tree_total_size=2**24, max_tree_nodes=2**16))
        tree.insert("aaaa", "old")
        time.sleep(0.02)
        tree.insert("bbbb", "mid")
        time.sleep(0.02)
        tree.insert("cccc", "new")

        # The budget only fits the most recent entry.
        tree.evict_to_global_size(4)

        sizes = tree.get_used_size_per_tenant()
        # The test has to name the entries that survive, not only count them.
        # The root of the tree keeps the name of every tenant that ever
        # inserted, but it holds no characters, therefore the sizes are the
        # ones that matter.
        self.assertEqual(sizes.get("old", 0), 0)
        self.assertEqual(sizes.get("mid", 0), 0)
        self.assertEqual(sizes.get("new"), 4)

    def test_the_least_recently_used_entry_is_evicted_first(self):
        tree = Tree(capacity=CapacityConfig(max_tree_total_size=2**24, max_tree_nodes=2**16))
        tree.insert("aaaa", "old")
        time.sleep(0.02)
        tree.insert("bbbb", "mid")
        time.sleep(0.02)
        tree.insert("cccc", "new")

        # A budget of eight characters fits two entries, the oldest one is
        # the one that is dropped.
        tree.evict_to_global_size(8)

        sizes = tree.get_used_size_per_tenant()
        self.assertEqual(sizes.get("old", 0), 0)
        self.assertEqual(sizes.get("mid"), 4)
        self.assertEqual(sizes.get("new"), 4)

    def test_the_global_budget_is_a_noop_when_within_the_limit(self):
        tree = Tree(capacity=CapacityConfig(max_tree_total_size=2**24, max_tree_nodes=2**16))
        tree.insert("aaaa", "tenant1")
        tree.evict_to_global_size(1000)
        self.assertEqual(tree.get_used_size_per_tenant().get("tenant1"), 4)

    def test_a_global_budget_of_zero_disables_the_limit(self):
        def fill(capacity):
            tree = Tree(capacity=capacity)
            for i in range(200):
                # Distinct texts, so that every insertion really adds
                # characters to the tree.
                tree.insert(f"{i:08d}-{'q' * 24}-{i * 7919 % 9973:04d}", f"tenant{i % 5}")
            return sum(tree.get_used_size_per_tenant().values())

        unlimited = fill(CapacityConfig(max_tree_total_size=0, max_tree_nodes=2**16))
        limited = fill(CapacityConfig(max_tree_total_size=1000, max_tree_nodes=2**16))

        self.assertLessEqual(limited, 1000)
        self.assertGreater(unlimited, limited)

    def test_the_eviction_does_not_break_the_tree(self):
        tree = Tree(capacity=CapacityConfig(max_tree_total_size=2**24, max_tree_nodes=2**16))
        for tenant_index in range(10):
            tree.insert(f"common-prefix-{tenant_index}", f"tenant{tenant_index}")
        tree.evict_to_global_size(5)
        # The tree must still answer lookups without raising.
        matched_text, tenant = tree.prefix_match("common-prefix-1")
        self.assertIsInstance(matched_text, str)
        self.assertIsInstance(tenant, str)


class TestWorkerQuota(unittest.TestCase):
    """The number of workers is bounded (CWE-770)."""

    @staticmethod
    def _router(capacity, backends=()):
        from launch_server import Router

        router = Router(capacity=capacity)
        for backend in backends:
            router.backends[backend] = object()
        return router

    def test_the_worker_limit_is_enforced(self):
        async def scenario():
            router = self._router(CapacityConfig(max_workers=2), ["http://a:8001", "http://b:8002"])

            async def unhealthy(urls, max_concurrency=10):
                return {url: False for url in urls}

            router.batch_check_url_health = unhealthy
            succeeded, failed, msg = await router.add_workers(["http://c:8003"])
            await router.session.close()
            return succeeded, failed, msg

        succeeded, failed, msg = asyncio.run(scenario())
        self.assertEqual(succeeded, [])
        self.assertEqual(failed, ["http://c:8003"])
        self.assertIn("Worker limit reached", msg)

    def test_the_worker_limit_allows_a_registration_below_the_quota(self):
        async def scenario():
            router = self._router(CapacityConfig(max_workers=2), ["http://a:8001"])

            async def unhealthy(urls, max_concurrency=10):
                return {url: False for url in urls}

            router.batch_check_url_health = unhealthy
            succeeded, failed, msg = await router.add_workers(["http://127.0.0.1:1"])
            await router.session.close()
            return succeeded, failed, msg

        succeeded, failed, msg = asyncio.run(scenario())
        # The URL is unreachable, but it was not rejected by the quota.
        self.assertNotIn("Worker limit reached", msg)

    def test_a_batch_registration_cannot_exceed_the_quota(self):
        """A single request must not be able to push the router over the quota.

        The quota was only checked once per request, before the batch was
        added. A request that carried many URLs therefore registered all of
        them and the number of workers grew far beyond the limit.
        """
        async def scenario():
            router = self._router(CapacityConfig(max_workers=3), ["http://a:8001"])

            async def healthy(urls, max_concurrency=10):
                return {url: True for url in urls}

            router.batch_check_url_health = healthy
            succeeded, failed, msg = await router.add_workers([f"http://w{i}:8001" for i in range(10)])
            registered = len(router.backends)
            await router.session.close()
            return succeeded, failed, registered

        succeeded, failed, registered = asyncio.run(scenario())
        self.assertLessEqual(registered, 3)
        self.assertEqual(len(succeeded), 2)
        # The URLs that did not fit into the quota are reported as failed.
        self.assertEqual(len(failed), 8)

    def test_concurrent_registrations_do_not_exceed_the_quota(self):
        """Two registrations that overlap must not both pass the quota check.

        The check and the commit were not serialised, so two concurrent
        requests could both see a free slot and together push the router
        over the quota.
        """

        async def scenario():
            router = self._router(CapacityConfig(max_workers=5))

            async def healthy(urls, max_concurrency=10):
                # Yield to the event loop, so that the two registrations
                # really overlap.
                await asyncio.sleep(0)
                return {url: True for url in urls}

            router.batch_check_url_health = healthy

            await asyncio.gather(
                router.add_workers([f"http://w{i}:8001" for i in range(4)]),
                router.add_workers([f"http://x{i}:8001" for i in range(4)]),
            )
            registered = len(router.backends)
            await router.session.close()
            return registered

        registered = asyncio.run(scenario())
        self.assertLessEqual(registered, 5)

    def test_a_worker_limit_of_zero_disables_the_limit(self):
        async def scenario():
            router = self._router(CapacityConfig(max_workers=0))

            async def healthy(urls, max_concurrency=10):
                return {url: True for url in urls}

            router.batch_check_url_health = healthy
            await router.add_workers([f"http://w{i}:8001" for i in range(20)])
            registered = len(router.backends)
            await router.session.close()
            return registered

        self.assertEqual(asyncio.run(scenario()), 20)

    def test_the_default_limit_is_bounded(self):
        self.assertGreater(CapacityConfig().max_workers, 0)
        self.assertLessEqual(CapacityConfig().max_workers, 10000)


if __name__ == "__main__":
    unittest.main()
