"""Regression tests for the console output of the prefix cache tree.

The tree used to print the prompts of all tenants and the state of the
eviction to stdout. The console output of the router ends up in the
container logs, which makes it a second, unprotected copy of the prompts of
every tenant. The output was moved to the debug log, where it only shows up
when the operator asked for it.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from src.tree import Tree


class _CaptureStdout:
    """Capture everything that is written to ``sys.stdout``."""

    def __init__(self):
        self.buffer = io.StringIO()

    def __enter__(self):
        self._redirect = redirect_stdout(self.buffer)
        self._redirect.__enter__()
        return self

    def __exit__(self, *args):
        self._redirect.__exit__(*args)
        return False

    @property
    def text(self):
        return self.buffer.getvalue()


class _CaptureStdoutFd:
    """Capture everything that is written to the file descriptor 1.

    ``redirect_stdout`` only catches the writes that go through
    ``sys.stdout``. A write that bypasses it (a C extension, a subprocess)
    would slip through, so the real file descriptor is captured as well.
    """

    def __enter__(self):
        self._saved_fd = os.dup(1)
        self._tmp = tempfile.TemporaryFile(mode="w+b")
        sys.stdout.flush()
        os.dup2(self._tmp.fileno(), 1)
        return self

    def __exit__(self, *args):
        sys.stdout.flush()
        os.dup2(self._saved_fd, 1)
        os.close(self._saved_fd)
        self._tmp.seek(0)
        self.data = self._tmp.read().decode("utf-8", "replace")
        self._tmp.close()
        return False


class TestTreeDoesNotWriteToStdout(unittest.TestCase):
    def test_the_eviction_does_not_write_to_stdout(self):
        tree = Tree()
        tree.insert("the secret prompt of a client", "tenant1")

        with _CaptureStdout() as capture:
            tree.evict_tenant_by_size(0)

        # The assertion is about the complete output, not only about the
        # absence of the prompt.
        self.assertEqual(capture.text, "")

    def test_pretty_print_does_not_write_to_stdout(self):
        tree = Tree()
        tree.insert("the secret prompt of a client", "tenant1")

        with _CaptureStdout() as capture:
            result, node_count = tree.pretty_print()

        self.assertEqual(capture.text, "")
        # The caller still gets the tree, only the console output is gone.
        self.assertIn("the secret prompt of a client", result)
        self.assertGreater(node_count, 0)

    def test_nothing_is_written_to_stdout_during_a_full_cycle(self):
        tree = Tree()
        for i in range(10):
            tree.insert(f"prompt number {i}", f"tenant{i}")

        with _CaptureStdout() as capture:
            tree.evict_tenant_by_size(1)
            tree.pretty_print()
            tree.evict_tenant_by_size(0)
            tree.pretty_print()

        self.assertEqual(capture.text, "")

    def test_nothing_is_written_to_the_stdout_file_descriptor(self):
        tree = Tree()
        for i in range(10):
            tree.insert(f"prompt number {i}", f"tenant{i}")

        with _CaptureStdoutFd() as capture:
            tree.evict_tenant_by_size(1)
            tree.pretty_print()

        self.assertEqual(capture.data, "")

    def test_the_eviction_state_is_available_in_the_debug_log(self):
        """The information is not lost, it moved to the debug log."""
        import logging

        from utils.logger import logger

        records = []

        class _Collector(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Collector(level=logging.DEBUG)
        previous_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            tree = Tree()
            tree.insert("the secret prompt of a client", "tenant1")
            with _CaptureStdout() as capture:
                tree.evict_tenant_by_size(0)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        self.assertEqual(capture.text, "")
        self.assertTrue(any("used size per tenant" in message for message in records))


if __name__ == "__main__":
    unittest.main()
