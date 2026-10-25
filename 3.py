import random
import time
import matplotlib.pyplot as plt
import sys
sys.setrecursionlimit(20000)

class Node:
    def __init__(self, key):
        self.left = None
        self.right = None
        self.val = key

class BinarySearchTree:
    def __init__(self):
        self.root = None

    # ---------- Вставка ----------
    def insert(self, key):
        if self.root is None:
            self.root = Node(key)
        else:
            self._insert(self.root, key)

    def _insert(self, node, key):
        if key < node.val:
            if node.left is None:
                node.left = Node(key)
            else:
                self._insert(node.left, key)
        else:  # если ключ >=, идём вправо
            if node.right is None:
                node.right = Node(key)
            else:
                self._insert(node.right, key)

    # ---------- Поиск ----------
    def search(self, key):
        return self._search(self.root, key)

    def _search(self, node, key):
        if node is None or node.val == key:
            return node
        if key < node.val:
            return self._search(node.left, key)
        return self._search(node.right, key)

    # ---------- Удаление ----------
    def delete(self, key):
        self.root = self._delete(self.root, key)

    def _delete(self, node, key):
        if node is None:
            return node
        if key < node.val:
            node.left = self._delete(node.left, key)
        elif key > node.val:
            node.right = self._delete(node.right, key)
        else:
            # узел с одним потомком или без потомков
            if node.left is None:
                return node.right
            elif node.right is None:
                return node.left
            # узел с двумя потомками: ищем минимальный в правом поддереве
            temp = self._min_value_node(node.right)
            node.val = temp.val
            node.right = self._delete(node.right, temp.val)
        return node

    def _min_value_node(self, node):
        current = node
        while current.left is not None:
            current = current.left
        return current

    # ---------- Высота дерева ----------
    def height(self):
        return self._height(self.root)

    def _height(self, node):
        if node is None:
            return -1  # высота пустого дерева = -1 (по числу рёбер)
        return 1 + max(self._height(node.left), self._height(node.right))

    # ---------- Обходы ----------
    def inorder_traversal(self):
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node, result):
        if node:
            self._inorder(node.left, result)
            result.append(node.val)
            self._inorder(node.right, result)

    def preorder_traversal(self):
        result = []
        self._preorder(self.root, result)
        return result

    def _preorder(self, node, result):
        if node:
            result.append(node.val)
            self._preorder(node.left, result)
            self._preorder(node.right, result)

    def postorder_traversal(self):
        result = []
        self._postorder(self.root, result)
        return result

    def _postorder(self, node, result):
        if node:
            self._postorder(node.left, result)
            self._postorder(node.right, result)
            result.append(node.val)

    # Построение сбалансированного дерева из отсортированного массива
    @staticmethod
    def from_sorted_array(arr):
        bst = BinarySearchTree()
        bst.root = bst._sorted_array_to_bst(arr, 0, len(arr) - 1)
        return bst

    def _sorted_array_to_bst(self, arr, start, end):
        if start > end:
            return None
        mid = (start + end) // 2
        node = Node(arr[mid])
        node.left = self._sorted_array_to_bst(arr, start, mid - 1)
        node.right = self._sorted_array_to_bst(arr, mid + 1, end)
        return node

def measure_search_time(bst, keys_to_search, repeats=1000):
    """Измеряет среднее время поиска (в микросекундах)."""
    start = time.perf_counter()
    for _ in range(repeats):
        key = random.choice(keys_to_search)
        bst.search(key)
    end = time.perf_counter()
    return (end - start) / repeats * 1e6

def measure_delete_time(bst, keys_to_delete, repeats=500):
    """Измеряет среднее время удаления и восстановления.
       После каждого удаления ключ вставляется обратно.
    """
    # Для точности лучше перестраивать дерево, но здесь используем упрощение
    total_time = 0
    for _ in range(repeats):
        key = random.choice(keys_to_delete)
        start = time.perf_counter()
        bst.delete(key)
        bst.insert(key)  # восстанавливаем
        total_time += (time.perf_counter() - start)
    return total_time / repeats * 1e6

# # Параметры эксперимента
# sizes = [1000, 5000, 10000]
# results = {'A': [], 'B': [], 'C': []}
#
# for N in sizes:
#     print(f"\nN = {N}")
#     # Данные
#     sorted_data = list(range(1, N+1))
#     shuffled_data = sorted_data.copy()
#     random.shuffle(shuffled_data)
#
#     # Дерево A (упорядоченная вставка)
#     bstA = BinarySearchTree()
#     for val in sorted_data:
#         bstA.insert(val)
#     hA = bstA.height()
#     # Выбираем 500 случайных существующих ключей для тестов
#     test_keys = random.sample(sorted_data, 500)
#     time_searchA = measure_search_time(bstA, test_keys)
#     time_delA = measure_delete_time(bstA, test_keys)
#
#     # Дерево B (случайная вставка)
#     bstB = BinarySearchTree()
#     for val in shuffled_data:
#         bstB.insert(val)
#     hB = bstB.height()
#     time_searchB = measure_search_time(bstB, test_keys)
#     time_delB = measure_delete_time(bstB, test_keys)
#
#     # Дерево C (идеально сбалансированное)
#     bstC = BinarySearchTree.from_sorted_array(sorted_data)
#     hC = bstC.height()
#     time_searchC = measure_search_time(bstC, test_keys)
#     time_delC = measure_delete_time(bstC, test_keys)
#
#     # Сохраняем
#     results['A'].append((N, hA, time_searchA, time_delA))
#     results['B'].append((N, hB, time_searchB, time_delB))
#     results['C'].append((N, hC, time_searchC, time_delC))
#
#     print(f"  A: высота={hA}, поиск={time_searchA:.2f} мкс, удаление={time_delA:.2f} мкс")
#     print(f"  B: высота={hB}, поиск={time_searchB:.2f} мкс, удаление={time_delB:.2f} мкс")
#     print(f"  C: высота={hC}, поиск={time_searchC:.2f} мкс, удаление={time_delC:.2f} мкс")
#
# # Построение графиков
# fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
#
# # Высота
# for label in ['A', 'B', 'C']:
#     x = [r[0] for r in results[label]]
#     y = [r[1] for r in results[label]]
#     ax1.plot(x, y, marker='o', label=f'Дерево {label}')
# ax1.set_xlabel('Размер N')
# ax1.set_ylabel('Высота')
# ax1.set_title('Высота деревьев')
# ax1.legend()
# ax1.grid(True)
#
# # Время поиска
# for label in ['A', 'B', 'C']:
#     x = [r[0] for r in results[label]]
#     y = [r[2] for r in results[label]]
#     ax2.plot(x, y, marker='o', label=f'Дерево {label}')
# ax2.set_xlabel('Размер N')
# ax2.set_ylabel('Среднее время поиска (мкс)')
# ax2.set_title('Время поиска')
# ax2.legend()
# ax2.grid(True)
# plt.yscale('log')  # логарифмическая шкала для времени A
# plt.tight_layout()
# plt.show()
