"""Regression tests for the masking of the debug output (CWE-200)."""

import contextlib
import io
import unittest

from src.tree import Tree, mask_node_text

SECRET = "机密内网API地址是 10.0.0.7:8443 token=sk-secret-value-12345"


class TestMaskNodeText(unittest.TestCase):
    def test_plaintext_is_not_revealed(self):
        masked = mask_node_text(SECRET)
        self.assertNotIn("sk-secret-value", masked)
        self.assertNotIn("10.0.0.7", masked)
        self.assertNotIn("机密", masked)

    def test_length_is_reported(self):
        self.assertIn(f"len={len(SECRET)}", mask_node_text(SECRET))

    def test_same_text_gives_the_same_digest(self):
        self.assertEqual(mask_node_text(SECRET), mask_node_text(SECRET))

    def test_different_text_gives_a_different_digest(self):
        self.assertNotEqual(mask_node_text(SECRET), mask_node_text("other"))

    def test_empty_text(self):
        self.assertIn("len=0", mask_node_text(""))

    def test_digest_is_salted(self):
        """The digest must not be the plain hash of the text.

        Without a salt the digest could be precomputed by everybody who can
        read the debug output: a short prompt would be recovered by hashing
        every candidate.
        """
        import hashlib

        plain = hashlib.sha256(SECRET.encode("utf-8")).hexdigest()[:8]
        self.assertNotIn(plain, mask_node_text(SECRET))

    def test_digest_is_stable_within_the_process(self):
        self.assertEqual(mask_node_text(SECRET), mask_node_text(SECRET))


class TestPrettyPrintDoesNotLeak(unittest.TestCase):
    def test_dump_contains_no_prompt(self):
        tree = Tree()
        tree.insert(SECRET, "http://worker1:8001")
        tree.insert("another secret prompt", "http://worker2:8002")

        dump, node_count = tree.pretty_print()

        self.assertNotIn("sk-secret-value", dump)
        self.assertNotIn("10.0.0.7", dump)
        self.assertNotIn("another secret prompt", dump)
        self.assertGreaterEqual(node_count, 2)

    def test_dump_is_not_printed_to_stdout(self):
        tree = Tree()
        tree.insert(SECRET, "http://worker1:8001")

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            tree.pretty_print()

        self.assertNotIn("sk-secret-value", buffer.getvalue())

    def test_eviction_does_not_print_to_stdout(self):
        tree = Tree()
        for i in range(20):
            tree.insert(f"prompt-{i}", "http://worker1:8001")

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            tree.evict_tenant_by_size(5)

        self.assertEqual(buffer.getvalue(), "")

    def test_structure_is_still_visible(self):
        tree = Tree()
        tree.insert(SECRET, "http://worker1:8001")
        dump, _ = tree.pretty_print()
        # The topology is still debuggable, only the prompt is masked.
        self.assertIn("http://worker1:8001", dump)
        self.assertIn("len=", dump)


if __name__ == "__main__":
    unittest.main()
