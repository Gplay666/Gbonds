import random
import time


class Node:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None
        self.height = 1  # высота узла (количество рёбер до самого глубокого листа)

class AVLTree:
    def __init__(self):
        self.root = None

    # ---------- Вспомогательные методы ----------
    def _get_height(self, node):
        return node.height if node else 0

    def _update_height(self, node):
        node.height = 1 + max(self._get_height(node.left), self._get_height(node.right))

    def _get_balance(self, node):
        return self._get_height(node.right) - self._get_height(node.left) if node else 0

    # ---------- Вращения ----------
    def _rotate_right(self, y):
        x = y.left
        T2 = x.right

        # вращение
        x.right = y
        y.left = T2

        # обновление высот
        self._update_height(y)
        self._update_height(x)

        return x

    def _rotate_left(self, x):
        y = x.right
        T2 = y.left

        # вращение
        y.left = x
        x.right = T2

        # обновление высот
        self._update_height(x)
        self._update_height(y)

        return y

    # ---------- Балансировка ----------
    def _balance(self, node):
        balance = self._get_balance(node)

        # левое поддерево тяжелее
        if balance < -1:
            if self._get_balance(node.left) <= 0:
                # левое-левое
                return self._rotate_right(node)
            else:
                # левое-правое
                node.left = self._rotate_left(node.left)
                return self._rotate_right(node)

        # правое поддерево тяжелее
        if balance > 1:
            if self._get_balance(node.right) >= 0:
                # правое-правое
                return self._rotate_left(node)
            else:
                # правое-левое
                node.right = self._rotate_right(node.right)
                return self._rotate_left(node)

        return node

    # ---------- Вставка ----------
    def insert(self, key):
        self.root = self._insert(self.root, key)

    def _insert(self, node, key):
        if node is None:
            return Node(key)

        if key < node.key:
            node.left = self._insert(node.left, key)
        elif key > node.key:
            node.right = self._insert(node.right, key)
        else:
            # дубликаты не вставляем
            return node

        self._update_height(node)
        return self._balance(node)

    # ---------- Поиск ----------
    def search(self, key):
        return self._search(self.root, key)

    def _search(self, node, key):
        if node is None or node.key == key:
            return node
        if key < node.key:
            return self._search(node.left, key)
        return self._search(node.right, key)

    # ---------- Удаление ----------
    def delete(self, key):
        self.root = self._delete(self.root, key)

    def _delete(self, node, key):
        if node is None:
            return node

        if key < node.key:
            node.left = self._delete(node.left, key)
        elif key > node.key:
            node.right = self._delete(node.right, key)
        else:
            # узел найден
            if node.left is None:
                return node.right
            elif node.right is None:
                return node.left
            else:
                # узел с двумя потомками: ищем минимальный в правом поддереве
                min_larger_node = self._get_min(node.right)
                node.key = min_larger_node.key
                node.right = self._delete(node.right, min_larger_node.key)

        if node is None:
            return node

        self._update_height(node)
        return self._balance(node)

    def _get_min(self, node):
        while node.left is not None:
            node = node.left
        return node

    # ---------- Обходы (для проверки) ----------
    def inorder(self):
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node, result):
        if node:
            self._inorder(node.left, result)
            result.append(node.key)
            self._inorder(node.right, result)

    # ---------- Высота дерева ----------
    def height(self):
        return self._get_height(self.root)


def measure_performance(tree_class, sizes, repeats=5, search_trials=10000):
    results = {size: {'height': 0, 'insert_time': 0, 'search_time': 0} for size in sizes}

    for size in sizes:
        search_trials = size*2
        for _ in range(repeats):
            # генерация уникальных ключей
            keys = random.sample(range(size * 10), size)
            tree = tree_class()

            # замер вставки
            start = time.perf_counter()
            for k in keys:
                tree.insert(k)
            insert_time = (time.perf_counter() - start) / size * 1e6  # мкс

            # высота
            height = tree.height()

            # замер поиска
            # подготовим запросы: половина существующих, половина случайных отсутствующих
            queries = random.sample(keys, search_trials // 2) + \
                      random.sample(range(size * 10, size * 20), search_trials // 2)
            # queries = random.choices(keys, k=search_trials // 2) + \
            #           random.choices(range(size * 10, size * 20), k=search_trials // 2)
            random.shuffle(queries)
            start = time.perf_counter()
            for q in queries:
                tree.search(q)
            search_time = (time.perf_counter() - start) / search_trials * 1e6

            # накопление
            results[size]['height'] += height / repeats
            results[size]['insert_time'] += insert_time / repeats
            results[size]['search_time'] += search_time / repeats

    return results
