import unittest
from unittest.mock import patch, MagicMock
import argparse
import sys
from typing import Dict

from pydantic import ValidationError

# 测试目标代码中的函数和类
from launch_server import parse_router_args, Router
from router.protocol import RouterArgs


class TestParseRouterArgs(unittest.TestCase):
    def setUp(self):
        # 保存原始sys.argv并清空
        self.original_argv = sys.argv
        sys.argv = [sys.argv[0]]  # 保留脚本名称

    def tearDown(self):
        # 恢复原始sys.argv
        sys.argv = self.original_argv

    def test_default_values(self):
        """测试所有参数的默认值"""
        args = parse_router_args()
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8008)
        self.assertEqual(args.worker_urls, ["http://127.0.0.1:8001", "http://127.0.0.1:8002"])
        self.assertEqual(args.policy, "cache_aware")
        self.assertEqual(args.worker_startup_timeout_secs, 30)
        self.assertEqual(args.worker_startup_check_interval, 1)
        self.assertEqual(args.cache_threshold, 0.5)
        self.assertEqual(args.balance_abs_threshold, 32)
        self.assertEqual(args.balance_rel_threshold, 1.0001)
        self.assertEqual(args.eviction_interval_secs, 60)
        self.assertEqual(args.max_tree_size, 2 ** 24)
        self.assertEqual(args.log_dir, "")
        self.assertFalse(args.verbose)

    def test_custom_values(self):
        """测试自定义参数值"""
        test_args = [
            "--host", "127.0.0.1",
            "--port", "8009",
            "--worker-urls", "http://worker1:8003", "http://worker2:8004",
            "--policy", "round_robin",
            "--worker-startup-timeout-secs", "60",
            "--worker-startup-check-interval", "5",
            "--cache-threshold", "0.7",
            "--balance-abs-threshold", "64",
            "--balance-rel-threshold", "1.5",
            "--eviction-interval-secs", "120",
            "--max-tree-size", "1000000",
            "--log-dir", "/var/log",
            "--verbose"
        ]
        sys.argv.extend(test_args)

        args = parse_router_args()
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8009)
        self.assertEqual(args.worker_urls, ["http://worker1:8003", "http://worker2:8004"])
        self.assertEqual(args.policy, "round_robin")
        self.assertEqual(args.worker_startup_timeout_secs, 60)
        self.assertEqual(args.worker_startup_check_interval, 5)
        self.assertEqual(args.cache_threshold, 0.7)
        self.assertEqual(args.balance_abs_threshold, 64)
        self.assertEqual(args.balance_rel_threshold, 1.5)
        self.assertEqual(args.eviction_interval_secs, 120)
        self.assertEqual(args.max_tree_size, 1000000)
        self.assertEqual(args.log_dir, "/var/log")
        self.assertTrue(args.verbose)

    def test_worker_urls_single_value(self):
        """测试单个worker URL"""
        sys.argv.extend(["--worker-urls", "http://single-worker:8000"])
        args = parse_router_args()
        self.assertEqual(args.worker_urls, ["http://single-worker:8000"])

    def test_invalid_cache_threshold(self):
        """测试无效的cache-threshold值"""
        # sys.argv.extend(["--cache-threshold", "1.2"])
        # router_args = parse_router_args()
        # with self.assertRaises(ValidationError) as cm:
        #     router_configuration = RouterArgs(**vars(router_args))

        test_cases = [
            ("1.5", "must be between 0.0 and 1.0"),  # 大于1.0
            ("-0.1", "must be between 0.0 and 1.0"),  # 小于0.0
        ]

        for value, expected_error in test_cases:
            with self.subTest(value=value, expected_error=expected_error):
                sys.argv = [sys.argv[0]]  # 重置参数
                sys.argv.extend(["--cache-threshold", value])

                with self.assertRaises(ValidationError) as cm:
                    router_args = parse_router_args()
                    router_configuration = RouterArgs(**vars(router_args))

    def test_missing_required_args(self):
        """测试缺少必需参数的情况"""
        # 这里没有必需参数，所有参数都有默认值
        args = parse_router_args()
        self.assertIsNotNone(args)


if __name__ == "__main__":
    unittest.main()
