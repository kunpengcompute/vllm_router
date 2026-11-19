"""Regression tests for the resource limits of the prefix cache tree (CWE-400)."""

import unittest

from src.tree import Tree


class TestNodeBudget(unittest.TestCase):
    def test_node_count_is_bounded(self):
        tree = Tree(max_nodes=64)
        for i in range(5000):
            # A shared prefix with a varying tail creates one node per request.
            tree.insert(f"{'A' * 64}{i:08d}-payload", "tenant1")
        self.assertLessEqual(tree.get_node_count(), 64)

    def test_tree_stays_usable_when_full(self):
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


class TestTextLengthLimit(unittest.TestCase):
    def test_text_is_truncated(self):
        tree = Tree(max_nodes=2**16, max_text_length=32)
        tree.insert("Z" * 4096, "tenant1")
        self.assertLessEqual(tree.get_used_size_per_tenant().get("tenant1", 0), 32)

    def test_short_text_is_untouched(self):
        tree = Tree(max_nodes=2**16, max_text_length=32)
        tree.insert("short", "tenant1")
        self.assertEqual(tree.get_used_size_per_tenant().get("tenant1"), 5)


class TestGetTextFromRequest(unittest.TestCase):
    def test_prompt_is_truncated(self):
        from launch_server import Router

        body = {"prompt": "p" * 10000}
        text = Router.get_text_from_request(body, "v1/completions", max_length=128)
        self.assertEqual(len(text), 128)

    def test_messages_are_truncated(self):
        from launch_server import Router

        body = {"messages": [{"role": "user", "content": "m" * 10000}]}
        text = Router.get_text_from_request(body, "v1/completions", max_length=64)
        self.assertEqual(len(text), 64)

    def test_short_prompt_is_untouched(self):
        from launch_server import Router

        body = {"prompt": "hello"}
        text = Router.get_text_from_request(body, "v1/completions", max_length=128)
        self.assertEqual(text, "hello")

    def test_missing_fields(self):
        from launch_server import Router

        self.assertEqual(Router.get_text_from_request({}, "v1/completions", max_length=128), "")
        self.assertEqual(Router.get_text_from_request({}, "generate", max_length=128), "")


if __name__ == "__main__":
    unittest.main()
