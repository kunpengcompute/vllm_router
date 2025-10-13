import random
import string
import threading
import time
import unittest
from typing import List

from src.tree import Tree


class TestTree(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()

    @staticmethod
    def random_string(length):
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

    def test_get_smallest_tenant(self):
        self.setUp()
        # Test empty tree
        self.assertEqual(self.tree.get_smallest_tenant(), "empty")

        # Insert data for tenant1 - "ap" + "icot" = 6 chars
        self.tree.insert("ap", "tenant1")
        self.tree.insert("icot", "tenant1")

        self.tree.insert("cat", "tenant2")
        time.sleep(0.1)

        # Test - tenant2 should be smallest with 3 chars vs 6 chars
        self.assertEqual(self.tree.get_smallest_tenant(), "tenant2",
                         "Expected tenant2 to be smallest with 3 characters.")

        # Insert overlapping data for tenant3 and tenant4 to test equal counts
        # tenant3: "do" = 2 chars
        # tenant4: "hi" = 2 chars
        self.tree.insert("do", "tenant3")
        self.tree.insert("hi", "tenant4")
        time.sleep(0.1)

        # should return either tenant3 or tenant4 (both have 2 chars)
        smallest = self.tree.get_smallest_tenant()
        self.assertTrue(smallest == "tenant3" or smallest == "tenant4",
                        f"Expected either tenant3 or tenant4 (both have 2 characters), got {smallest}")

        # Add more text to tenant4 to make it larger
        self.tree.insert("hello", "tenant4")  # Now tenant4 has "hi" + "hello" = 6 chars

        # Now tenant3 should be smallest (2 chars vs 6 chars for tenant4)
        self.assertEqual(self.tree.get_smallest_tenant(), "tenant3",
                         "Expected tenant3 to be smallest with 2 characters.")
        self.tree.pretty_print()
        # Test eviction
        self.tree.evict_tenant_by_size(3)  # This should evict tenants with more than 3 chars
        post_eviction_smallest = self.tree.get_smallest_tenant()
        print(f"Smallest tenant after eviction: {post_eviction_smallest}")

        self.tree.pretty_print()

    def test_tenant_char_count(self):
        self.setUp()

        # Phase 1: Initial insertions
        self.tree.insert("apple", "tenant1")
        self.tree.insert("apricot", "tenant1")
        self.tree.insert("banana", "tenant1")
        self.tree.insert("amplify", "tenant2")
        self.tree.insert("application", "tenant2")
        self.tree.pretty_print()

        computed_sizes = self.tree.get_used_size_per_tenant()
        maintained_counts = self.tree.tenant_char_count

        print("Phase 1 - Maintained vs Computed counts:")
        print(f"Maintained: {maintained_counts}\nComputed: {computed_sizes}")
        self.assertEqual(maintained_counts, computed_sizes, "Phase 1: Initial insertions")

        # Phase 2: Additional insertions
        self.tree.insert("apartment", "tenant1")
        self.tree.insert("appetite", "tenant2")
        self.tree.insert("ball", "tenant1")
        self.tree.insert("box", "tenant2")

        computed_sizes = self.tree.get_used_size_per_tenant()
        maintained_counts = self.tree.tenant_char_count

        print("Phase 2 - Maintained vs Computed counts:")
        print(f"Maintained: {maintained_counts}\nComputed: {computed_sizes}")
        self.assertEqual(maintained_counts, computed_sizes, "Phase 2: Additional insertions")

        # Phase 3: Overlapping insertions
        self.tree.insert("zebra", "tenant1")
        self.tree.insert("zebra", "tenant2")
        self.tree.insert("zero", "tenant1")
        self.tree.insert("zero", "tenant2")

        computed_sizes = self.tree.get_used_size_per_tenant()
        maintained_counts = self.tree.tenant_char_count

        print("Phase 3 - Maintained vs Computed counts:")
        print(f"Maintained: {maintained_counts}\nComputed: {computed_sizes}")
        self.assertEqual(maintained_counts, computed_sizes, "Phase 3: Overlapping insertions")

        # Phase 4: Eviction test
        self.tree.evict_tenant_by_size(10)
        computed_sizes = self.tree.get_used_size_per_tenant()
        maintained_counts = self.tree.tenant_char_count

        print("Phase 4 - Maintained vs Computed counts:")
        print(f"Maintained: {maintained_counts}\nComputed: {computed_sizes}")
        self.assertEqual(maintained_counts, computed_sizes, "Phase 4: After eviction")

    def test_cold_start(self):
        self.setUp()

        matched_text, tenant = self.tree.prefix_match("hello")
        self.assertEqual(matched_text, "")
        self.assertEqual(tenant, "empty")

    def test_exact_match_seq(self):
        self.setUp()
        self.tree.insert("hello", "tenant1")
        self.tree.pretty_print()
        self.tree.insert("apple", "tenant2")
        self.tree.pretty_print()
        self.tree.insert("banana", "tenant3")
        self.tree.pretty_print()

        matched_text, tenant = self.tree.prefix_match("hello")
        self.assertEqual(matched_text, "hello")
        self.assertEqual(tenant, "tenant1")

        matched_text, tenant = self.tree.prefix_match("apple")
        self.assertEqual(matched_text, "apple")
        self.assertEqual(tenant, "tenant2")

        matched_text, tenant = self.tree.prefix_match("banana")
        self.assertEqual(matched_text, "banana")
        self.assertEqual(tenant, "tenant3")

    def test_exact_match_concurrent(self):
        self.setUp()

        texts = ["hello", "apple", "banana"]
        tenants = ["tenant1", "tenant2", "tenant3"]

        # 并发插入
        insert_handles: List[threading.Thread] = []
        for i in range(3):
            thread = threading.Thread(target=self.tree.insert, args=(texts[i], tenants[i]))
            insert_handles.append(thread)
            thread.start()

        # 等待所有插入线程完成
        for handle in insert_handles:
            handle.join()

        # 并发匹配
        match_handles: List[threading.Thread] = []

        def match_task(text: str, tenant: str):
            matched_text, matched_tenant = self.tree.prefix_match(text)
            self.assertTrue(matched_text == text, f"Expected {text}, got {matched_text}")
            self.assertTrue(matched_tenant == tenant, f"Expected {tenant}, got {matched_tenant}")

        for i in range(3):
            thread = threading.Thread(target=match_task, args=(texts[i], tenants[i]))
            match_handles.append(thread)
            thread.start()

        # 等待所有匹配线程完成
        for handle in match_handles:
            handle.join()

    def test_partial_match_concurrent(self):
        self.setUp()

        texts = ["hello", "apple", "banana"]

        insert_handles: List[threading.Thread] = []
        for i in range(3):
            thread = threading.Thread(target=self.tree.insert, args=(texts[i], "tenant0"))
            insert_handles.append(thread)
            thread.start()

        # 等待所有插入线程完成
        for handle in insert_handles:
            handle.join()

        # 并发匹配
        match_handles: List[threading.Thread] = []

        def match_task(expected_text: str, expected_tenant: str):
            matched_text, matched_tenant = self.tree.prefix_match(expected_text)
            self.assertTrue(matched_text == expected_text, f"Expected matched text {expected_text}, got {matched_text}")
            self.assertTrue(matched_tenant == expected_tenant,
                            f"Expected tenant {expected_tenant}, got {matched_tenant}")

        for i in range(3):
            thread = threading.Thread(target=match_task, args=(texts[i], "tenant0"))
            match_handles.append(thread)
            thread.start()

        for handle in match_handles:
            handle.join()

    def test_group_prefix_insert_match_concurrent(self):
        self.setUp()

        prefix = [
            "Clock strikes midnight, I'm still wide awake",
            "Got dreams bigger than these city lights",
            "Time waits for no one, gotta make my move",
            "Started from the bottom, that's no metaphor",
        ]
        suffix = [
            "Got too much to prove, ain't got time to lose",
            "History in the making, yeah, you can't erase this",
        ]

        insert_handles: List[threading.Thread] = []
        for i in range(len(prefix)):
            for j in range(len(suffix)):
                text = f"{prefix[i]}, {suffix[j]}"
                tenant = f"tenant{i}"

                thread = threading.Thread(target=self.tree.insert, args=(text, tenant))
                insert_handles.append(thread)
                thread.start()

        for handle in insert_handles:
            handle.join()

        self.tree.pretty_print()

        # 使用多线程检查匹配
        match_handles: List[threading.Thread] = []

        def match_task(expected_text: str, expected_tenant: str):
            matched_text, matched_tenant = self.tree.prefix_match(expected_text)
            self.assertTrue(matched_text == expected_text, f"Expected matched text {expected_text}, got {matched_text}")
            self.assertTrue(matched_tenant == expected_tenant,
                            f"Expected tenant {expected_tenant}, got {matched_tenant}")

        for i in range(len(prefix)):
            thread = threading.Thread(target=match_task, args=(prefix[i], f"tenant{i}"))
            match_handles.append(thread)
            thread.start()

        for handle in match_handles:
            handle.join()

    def test_mixed_concurrent_insert_match(self):
        self.setUp()

        prefix = [
            "Clock strikes midnight, I'm still wide awake",
            "Got dreams bigger than these city lights",
            "Time waits for no one, gotta make my move",
            "Started from the bottom, that's no metaphor",
        ]
        suffix = [
            "Got too much to prove, ain't got time to lose",
            "History in the making, yeah, you can't erase this",
        ]

        handles: List[threading.Thread] = []

        for i in range(len(prefix)):
            for j in range(len(suffix)):
                text = f"{prefix[i]}, {suffix[j]}"
                tenant = f"tenant{i}"

                thread = threading.Thread(target=self.tree.insert, args=(text, tenant))
                handles.append(thread)
                thread.start()

        for handle in handles:
            handle.join()

        def match_task(expected_text: str):
            self.tree.prefix_match(expected_text)

        # 使用多线程检查匹配
        for i in prefix:
            thread = threading.Thread(target=match_task, args=(i,))
            handles.append(thread)
            thread.start()

        for handle in handles:
            handle.join()

    def test_simple_eviction(self):
        self.setUp()

        max_size = 5

        self.tree.insert("hello", "tenant1")  # size 5
        self.tree.insert("hello", "tenant2")  # size 5

        time.sleep(1)

        self.tree.insert("world", "tenant2")  # size 5  total for tenant2 = 10

        self.tree.pretty_print()

        sizes_before = self.tree.get_used_size_per_tenant()

        self.assertTrue(sizes_before.get("tenant1") == 5, "tenant1 should use 5 characters")
        self.assertTrue(sizes_before.get("tenant2") == 10, "tenant2 should use 10 characters")

        # 触发驱逐逻辑：tenant2 的最大可用空间是 5
        self.tree.evict_tenant_by_size(max_size)

        self.tree.pretty_print()

        sizes_after = self.tree.get_used_size_per_tenant()

        self.assertTrue(sizes_after.get("tenant1") == 5, "tenant1 should remain unchanged")
        self.assertTrue(sizes_after.get("tenant2") == 5, "tenant2 should have only 'world' left")

        self.tree.pretty_print()

        # 验证 "world" 是否仍然存在且归属 tenant2
        matched_text, matched_tenant = self.tree.prefix_match("world")
        self.assertTrue(matched_text == "world", f"Expected world, got {matched_text}")
        self.assertTrue(matched_tenant == "tenant2", f"Expected tenant2, got {matched_tenant}")

    def test_advanced_eviction(self):
        self.setUp()

        max_size = 100

        prefixes = ["aqwefcisdf", "iajsdfkmade", "kjnzxcvewqe", "iejksduqasd"]

        for i in range(100):
            for j, prefix in enumerate(prefixes):
                random_suffix = self.random_string(10)
                text = f"{prefix}{random_suffix}"
                tenant = f"tenant{j + 1}"
                self.tree.insert(text, tenant)

        self.tree.evict_tenant_by_size(max_size)

        # 执行踢出后检查大小
        sizes_after = self.tree.get_used_size_per_tenant()
        # 确保每个tenant的带下都在max_size以内
        for tenant, size in sizes_after.items():
            self.assertTrue(size <= max_size,
                            f"Tenant {tenant} exceeds size limit. Current size: {size}, Limit: {max_size}")

    def test_concurrent_operations_with_eviction(self):
        self.setUp()

        handles = []
        test_duration = 10
        start_time = time.time()
        max_size = 100

        # 生成踢出操作线程
        def eviction_task():
            while time.time() - start_time < test_duration:
                self.tree.evict_tenant_by_size(max_size)
                time.sleep(5)

        eviction_thread = threading.Thread(target=eviction_task)
        eviction_thread.daemon = True
        eviction_thread.start()
        handles.append(eviction_thread)

        # 生成工作线程
        def work_task(thread_id: int):
            rng = random.Random()
            tenant = f"tenant{thread_id + 1}"
            prefix = f"prefix{thread_id}"

            while time.time() - start_time < test_duration:
                # 随机决策：70%概率执行匹配操作，30%概率执行插入操作
                if rng.random() < 0.7:
                    random_len = rng.randint(3, 9)
                    search_str = prefix + self.random_string(random_len)
                    _matched, _ = self.tree.prefix_match(search_str)
                else:
                    random_len = rng.randint(5, 14)
                    insert_str = prefix + self.random_string(random_len)
                    self.tree.insert(insert_str, tenant)

                time.sleep(rng.uniform(0.01, 0.1))

        for thread_id in range(4):
            thread = threading.Thread(target=work_task, args=(thread_id,))
            thread.start()
            handles.append(thread)

        for handle in handles:
            handle.join()

        # 执行踢出操作
        self.tree.evict_tenant_by_size(max_size)

        final_sizes = self.tree.get_used_size_per_tenant()
        print(f"Final sizes after test completion: {final_sizes}")

        for tenant, size in final_sizes.items():
            self.assertTrue(size <= max_size, f"Tenant '{tenant}' exceeds limit: {size} > {max_size}")

    def test_leaf_of(self):
        self.setUp()

        # 单一节点
        self.tree.insert("hello", "tenant1")
        leaves = self.tree.leaf_of(self.tree.root.children.get("h"))
        self.assertEqual(leaves, ["tenant1"])

        # 多租户节点
        self.tree.insert("hello", "tenant2")
        leaves = self.tree.leaf_of(self.tree.root.children.get("h"))
        self.assertEqual(len(leaves), 2)
        self.assertTrue("tenant1" in leaves)
        self.assertTrue("tenant2" in leaves)

    def test_get_used_size_per_tenant(self):
        self.setUp()

        # 单一租户
        self.tree.insert("hello", "tenant1")
        self.tree.insert("world", "tenant1")
        sizes = self.tree.get_used_size_per_tenant()

        self.tree.pretty_print()
        print(sizes)
        self.assertEqual(sizes.get("tenant1"), 10)

        # 多租户共享节点
        self.tree.insert("hello", "tenant2")
        self.tree.insert("help", "tenant2")
        sizes = self.tree.get_used_size_per_tenant()

        self.tree.pretty_print()
        print(sizes)
        self.assertEqual(sizes.get("tenant1"), 10)
        self.assertEqual(sizes.get("tenant2"), 6)

    def test_prefix_match_tenant(self):
        self.setUp()

        self.tree.insert("hello", "tenant1")  # tenant1: hello
        self.tree.insert("hello", "tenant2")  # tenant2: hello
        self.tree.insert("hello world", "tenant2")  # tenant2: hello -> world
        self.tree.insert("help", "tenant1")  # tenant1: hel -> p
        self.tree.insert("helicopter", "tenant2")  # tenant2: hel -> icopter

        # 测试tenant1的数据
        self.assertEqual(self.tree.prefix_match_tenant("hello", "tenant1"), "hello")  # Full match for tenant1
        self.assertEqual(self.tree.prefix_match_tenant("help", "tenant1"), "help")  # Exclusive to tenant1
        self.assertEqual(self.tree.prefix_match_tenant("hel", "tenant1"), "hel")  # Shared prefix
        self.assertEqual(self.tree.prefix_match_tenant("hello world", "tenant1"),
                         "hello")  # Should stop at tenant1's boundary
        self.assertEqual(self.tree.prefix_match_tenant("helicopter", "tenant1"),
                         "hel")  # Should stop at tenant1's boundary

        # 测试tenant2的数据
        self.assertEqual(self.tree.prefix_match_tenant("hello", "tenant2"), "hello")  # Full match for tenant2
        self.assertEqual(self.tree.prefix_match_tenant("hello world", "tenant2"), "hello world")  # Exclusive to tenant2
        self.assertEqual(self.tree.prefix_match_tenant("helicopter", "tenant2"), "helicopter")  # Exclusive to tenant2
        self.assertEqual(self.tree.prefix_match_tenant("hel", "tenant2"), "hel")  # Shared prefix
        self.assertEqual(self.tree.prefix_match_tenant("help", "tenant2"), "hel")  # Should stop at tenant2's boundary

        # 测试不存在的租户
        self.assertEqual(self.tree.prefix_match_tenant("hello", "tenant3"), "")  # Non-existent tenant
        self.assertEqual(self.tree.prefix_match_tenant("help", "tenant3"), "")  # Non-existent tenant

    def test_simple_tenant_eviction(self):
        self.setUp()

        self.tree.insert("hello", "tenant1")
        self.tree.insert("world", "tenant1")
        self.tree.insert("hello", "tenant2")
        self.tree.insert("help", "tenant2")

        initial_sizes = self.tree.get_used_size_per_tenant()
        self.assertEqual(initial_sizes.get("tenant1"), 10)
        self.assertEqual(initial_sizes.get("tenant2"), 6)

        # 踢出tenant1
        self.tree.remove_tenant("tenant1")
        final_sizes = self.tree.get_used_size_per_tenant()
        self.assertTrue("tenant1" not in final_sizes, "tenant1 should be completely removed")
        self.assertTrue(final_sizes.get("tenant2") == 6, "tenant2 should be unaffected")

        # 验证tenant1的数据已不可获取
        self.assertEqual(self.tree.prefix_match_tenant("hello", "tenant1"), "")
        self.assertEqual(self.tree.prefix_match_tenant("world", "tenant1"), "")

    def test_complex_tenant_eviction(self):
        self.setUp()

        # 使用共享前缀创建更复杂的树结构
        self.tree.insert("apple", "tenant1")
        self.tree.insert("application", "tenant1")
        self.tree.insert("apple", "tenant2")
        self.tree.insert("appetite", "tenant2")
        self.tree.insert("banana", "tenant1")
        self.tree.insert("banana", "tenant2")
        self.tree.insert("ball", "tenant2")

        # 验证初始化状态
        initial_sizes = self.tree.get_used_size_per_tenant()
        print(f"Initial sizes: {initial_sizes}")
        self.tree.pretty_print()

        # 移除租户1
        self.tree.remove_tenant("tenant1")

        # 验证状态
        final_sizes = self.tree.get_used_size_per_tenant()
        print(f"Final sizes: {final_sizes}")
        self.tree.pretty_print()

        # 验证租户1已完全移除
        self.assertTrue("tenant1" not in final_sizes, "tenant1 should be completely removed")

        # 验证tenant1的数据已不可获取
        self.assertEqual(self.tree.prefix_match_tenant("apple", "tenant1"), "")
        self.assertEqual(self.tree.prefix_match_tenant("application", "tenant1"), "")
        self.assertEqual(self.tree.prefix_match_tenant("banana", "tenant1"), "")

        # 验证tenant2的数据仍完好
        self.assertEqual(self.tree.prefix_match_tenant("apple", "tenant2"), "apple")
        self.assertEqual(self.tree.prefix_match_tenant("appetite", "tenant2"), "appetite")
        self.assertEqual(self.tree.prefix_match_tenant("banana", "tenant2"), "banana")
        self.assertEqual(self.tree.prefix_match_tenant("ball", "tenant2"), "ball")

        # 验证树结构对租户2是否仍然有效
        tenant2_size = final_sizes.get("tenant2")
        self.assertEqual(tenant2_size, 5 + 5 + 6 + 2)  # "apple" + "etite" + "banana" + "ll"


if __name__ == "__main__":
    unittest.main()
