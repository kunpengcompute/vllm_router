import unittest
from unittest.mock import Mock, patch, MagicMock
from copy import deepcopy

from src.tree import Tree

# 假设你的主模块名为 router（根据文件路径调整导入）
from src.router import (
    RoundRobinRouter,
    RandomRouter,
    CacheAwareRouter,
    RouteSelector,
    RandomConfig,
    RoundRobinConfig,
    CacheAwareConfig,
    NoAvailableWorkerError,
    Tree
)


class DummyTree:
    """简易 Tree mock，用于 CacheAwareRouter 测试"""
    def __init__(self):
        self.tenants = set()
        self.matches = {}

    def insert(self, text: str, worker_url: str):
        self.tenants.add(worker_url)

    def remove_tenant(self, worker_url: str):
        self.tenants.discard(worker_url)

    def prefix_match(self, text: str):
        # 简化：总是返回空匹配，除非特别设置
        if hasattr(self, '_mock_match') and text in self._mock_match:
            matched_text, worker = self._mock_match[text]
            return matched_text, worker
        return "", next(iter(self.tenants)) if self.tenants else ""

    def get_smallest_tenant(self):
        return next(iter(self.tenants)) if self.tenants else ""

    def evict_tenant_by_size(self, max_size: int):
        pass  # no-op for test


class TestRouters(unittest.TestCase):

    def setUp(self):
        self.worker_urls = ["http://worker1", "http://worker2", "http://worker3"]

    # ======================
    # RoundRobinRouter Tests
    # ======================
    def test_round_robin_select(self):
        router = RoundRobinRouter(
            worker_urls=deepcopy(self.worker_urls),
            current_index=0,
            timeout_secs=300,
            interval_secs=10
        )
        first = router.select_generate_worker()
        second = router.select_generate_worker()
        self.assertNotEqual(first, second)
        self.assertIn(first, self.worker_urls)
        self.assertIn(second, self.worker_urls)

    def test_round_robin_empty_workers(self):
        router = RoundRobinRouter(
            worker_urls=[],
            current_index=0,
            timeout_secs=300,
            interval_secs=10
        )
        with self.assertRaises(NoAvailableWorkerError):
            router.select_generate_worker()

    # ===================
    # RandomRouter Tests
    # ===================
    @patch('src.router.random.randint')
    def test_random_select(self, mock_randint):
        mock_randint.return_value = 1
        router = RandomRouter(
            worker_urls=deepcopy(self.worker_urls),
            timeout_secs=300,
            interval_secs=10
        )
        selected = router.select_generate_worker()
        self.assertEqual(selected, "http://worker2")

    def test_random_empty_workers(self):
        router = RandomRouter(
            worker_urls=[],
            timeout_secs=300,
            interval_secs=10
        )
        with self.assertRaises(NoAvailableWorkerError):
            router.select_generate_worker()

    # ========================
    # CacheAwareRouter Tests
    # ========================
    def test_cache_aware_balanced_route_by_cache(self):
        tree = Tree()
        for url in self.worker_urls:
            tree.insert("", url)
        tree.insert("hello", self.worker_urls[0])
        router = CacheAwareRouter(
            worker_urls=deepcopy(self.worker_urls),
            tree=tree,
            running_queue={url: 0 for url in self.worker_urls},
            processed_queue={url: 0 for url in self.worker_urls},
            cache_threshold=0.4,  # len("hello")/len("hello world") ≈ 0.5 > 0.4
            balance_abs_threshold=10,
            balance_rel_threshold=1.5,
            timeout_secs=300,
            interval_secs=10
        )
        print(router.tree.pretty_print())
        selected = router.select_generate_worker("hello world")
        self.assertEqual(selected, "http://worker1")  # highest match

    def test_cache_aware_balanced_route_by_tree_size(self):
        tree = Tree()
        for url in self.worker_urls:
            tree.insert("", url)
        tree.insert("hello", self.worker_urls[0])
        tree.insert("world", self.worker_urls[2])
        # No good match → use smallest tenant
        expected_worker = self.worker_urls[1]

        router = CacheAwareRouter(
            worker_urls=deepcopy(self.worker_urls),
            tree=tree,
            running_queue={url: 5 for url in self.worker_urls},
            processed_queue={url: 10 for url in self.worker_urls},
            cache_threshold=0.9,
            balance_abs_threshold=10,
            balance_rel_threshold=1.5,
            timeout_secs=300,
            interval_secs=10
        )

        selected = router.select_generate_worker("unseen text")
        self.assertEqual(selected, expected_worker)

    def test_cache_aware_imbalanced_route_by_load(self):
        tree = Tree()
        tree.tenants = set(self.worker_urls)

        router = CacheAwareRouter(
            worker_urls=deepcopy(self.worker_urls),
            tree=tree,
            running_queue={"http://worker1": 100, "http://worker2": 10, "http://worker3": 10},
            processed_queue={url: 0 for url in self.worker_urls},
            cache_threshold=0.5,
            balance_abs_threshold=50,   # 100-10=90 > 50
            balance_rel_threshold=1.1,  # 100 > 10*1.1 → True
            timeout_secs=300,
            interval_secs=10
        )

        selected = router.select_generate_worker("any text")
        self.assertEqual(selected, "http://worker2")  # shortest queue

    def test_cache_aware_empty_workers(self):
        tree = Tree()
        router = CacheAwareRouter(
            worker_urls=[],
            tree=tree,
            running_queue={},
            processed_queue={},
            cache_threshold=0.5,
            balance_abs_threshold=10,
            balance_rel_threshold=1.1,
            timeout_secs=300,
            interval_secs=10
        )
        with self.assertRaises(NoAvailableWorkerError):
            router.select_generate_worker("test")

    # =====================
    # RouteSelector Tests
    # =====================
    @patch('src.router.Tree', Tree)
    def test_create_random_router(self):
        selector = RouteSelector()
        config = RandomConfig()
        router = selector.create_router(self.worker_urls, config)
        self.assertIsInstance(router, RandomRouter)
        self.assertEqual(router.worker_urls, self.worker_urls)

    @patch('src.router.Tree', Tree)
    def test_create_round_robin_router(self):
        selector = RouteSelector()
        config = RoundRobinConfig()
        router = selector.create_router(self.worker_urls, config)
        self.assertIsInstance(router, RoundRobinRouter)
        self.assertEqual(router.current_index, 0)

    @patch('src.router.Tree', Tree)
    def test_create_cache_aware_router(self):
        selector = RouteSelector()
        config = CacheAwareConfig()
        router = selector.create_router(self.worker_urls, config)
        self.assertIsInstance(router, CacheAwareRouter)
        self.assertEqual(set(router.running_queue.keys()), set(self.worker_urls))
        self.assertTrue(router._eviction_thread.is_alive())
        self.assertIsNotNone(router._stop_eviction)

    def test_update_router_add_remove(self):
        selector = RouteSelector()
        router = RoundRobinRouter(
            worker_urls=["http://w1"],
            current_index=0,
            timeout_secs=300,
            interval_secs=10
        )

        selector.update_router("http://w2", router, add=True)
        self.assertIn("http://w2", router.worker_urls)

        selector.update_router("http://w1", router, add=False)
        self.assertNotIn("http://w1", router.worker_urls)

    @patch('src.router.Tree', Tree)
    async def test_add_remove_worker_cache_aware(self):
        selector = RouteSelector()
        config = CacheAwareConfig()
        router = selector.create_router(["http://w1"], config)

        success, msg = await selector.add_worker("http://w2", router)
        self.assertTrue(success)
        self.assertIn("http://w2", router.worker_urls)
        self.assertIn("http://w2", router.running_queue)

        selector.remove_worker("http://w1", router)
        self.assertNotIn("http://w1", router.worker_urls)
        self.assertNotIn("http://w1", router.running_queue)

    def test_get_worker_urls_thread_safe(self):
        selector = RouteSelector()
        router = RandomRouter(worker_urls=self.worker_urls, timeout_secs=300, interval_secs=10)
        urls = selector.get_worker_urls(router)
        self.assertEqual(urls, self.worker_urls)
        self.assertIsNot(urls, router.worker_urls)  # deep copy


if __name__ == '__main__':
    unittest.main()
