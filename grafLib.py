from collections import deque
class Graph:
    """
    Простая библиотека для работы с графами.
    Поддерживает ориентированные и неориентированные графы, взвешенные рёбра.
    """

    def __init__(self, directed=False):
        """
        Инициализация пустого графа.
        :param directed: True для ориентированного графа, False для неориентированного.
        """
        self.directed = directed
        self.nodes = {}      # список смежности: {узел: [соседи]}
        self.edges = []      # список рёбер: [{'from': a, 'to': b, 'weight': w}]
        # Индекс рёбер для O(1) проверки дубликатов. Без него загрузка
        # библиотеки на 24k рёбер квадратично перебирала весь список.
        self._edge_lookup = {}

    def _rebuild_edge_lookup(self):
        """Пересобирает индекс после массового удаления рёбер."""
        self._edge_lookup = {}
        for edge in self.edges:
            self._edge_lookup[(edge['from'], edge['to'])] = edge
            if not self.directed:
                self._edge_lookup[(edge['to'], edge['from'])] = edge

    def clear_edges(self):
        """Удаляет все рёбра, сохраняя вершины."""
        for node in self.nodes:
            self.nodes[node] = []
        self.edges = []
        self._edge_lookup = {}

    def add_node(self, node):
        """Добавляет вершину в граф, если она ещё не существует."""
        if node not in self.nodes:
            self.nodes[node] = []
        # Если вершина уже есть, ничего не делаем (можно было бы кинуть исключение, но по умолчанию игнорируем)

    def add_edge(self, from_node, to_node, weight=1):
        """
        Добавляет ребро между from_node и to_node с заданным весом.
        Если граф неориентированный, добавляется также обратное ребро.
        Повторное добавление того же ребра не создаёт дубликат: вес
        обновляется до максимального.
        """
        if from_node not in self.nodes or to_node not in self.nodes:
            raise ValueError("Обе вершины должны существовать в графе. Сначала добавьте их через add_node.")

        # Если ребро уже есть — обновляем вес, а не плодим дубликаты.
        existing = self._edge_lookup.get((from_node, to_node))
        if existing is None and not self.directed:
            existing = self._edge_lookup.get((to_node, from_node))
        if existing is not None:
            existing['weight'] = max(existing['weight'], weight)
            return

        # Добавляем в список смежности
        if to_node not in self.nodes[from_node]:
            self.nodes[from_node].append(to_node)
        if not self.directed and from_node not in self.nodes[to_node]:
            self.nodes[to_node].append(from_node)

        # Добавляем в список рёбер. Для неориентированного графа храним
        # обе ориентации в индексе: так поиск работает при любом порядке.
        edge = {'from': from_node, 'to': to_node, 'weight': weight}
        self.edges.append(edge)
        self._edge_lookup[(from_node, to_node)] = edge
        if not self.directed:
            self._edge_lookup[(to_node, from_node)] = edge

    def remove_node(self, node):
        """Удаляет вершину и все связанные с ней рёбра."""
        if node not in self.nodes:
            raise ValueError(f"Вершина {node} не найдена.")

        # Удаляем все рёбра, содержащие эту вершину
        self.edges = [e for e in self.edges if e['from'] != node and e['to'] != node]
        self._rebuild_edge_lookup()

        # Удаляем вершину из списка смежности
        del self.nodes[node]

        # Удаляем все упоминания этой вершины в списках смежности других вершин
        for other in self.nodes:
            self.nodes[other] = [n for n in self.nodes[other] if n != node]

    def remove_incident_edges(self, node):
        """Удаляет все рёбра вершины, оставляя саму вершину в графе."""
        if node not in self.nodes:
            return 0
        before = len(self.edges)
        self.edges = [
            edge
            for edge in self.edges
            if edge['from'] != node and edge['to'] != node
        ]
        self._rebuild_edge_lookup()
        for other in self.nodes:
            if other != node:
                self.nodes[other] = [
                    neighbor
                    for neighbor in self.nodes[other]
                    if neighbor != node
                ]
        self.nodes[node] = []
        return before - len(self.edges)

    def remove_edge(self, from_node, to_node, weight=None):
        """
        Удаляет ребро между from_node и to_node.
        Если вес указан, удаляется только ребро с таким весом.
        Если не указан, удаляется первое попавшееся ребро между этими вершинами.
        Для неориентированного графа удаляется ребро в любом направлении.
        """
        # Поиск ребра через индекс.
        removed_edge = self._edge_lookup.get((from_node, to_node))
        if removed_edge is None and not self.directed:
            removed_edge = self._edge_lookup.get((to_node, from_node))

        if removed_edge is None or (
            weight is not None and removed_edge['weight'] != weight
        ):
            raise ValueError(f"Ребро между {from_node} и {to_node} не найдено.")

        self.edges.remove(removed_edge)
        self._edge_lookup.pop((removed_edge['from'], removed_edge['to']), None)
        if not self.directed:
            self._edge_lookup.pop((removed_edge['to'], removed_edge['from']), None)

        # Удаляем из списков смежности
        # В ориентированном графе удаляем только из from_node
        if from_node in self.nodes:
            self.nodes[from_node] = [n for n in self.nodes[from_node] if n != to_node]
        if not self.directed and to_node in self.nodes:
            self.nodes[to_node] = [n for n in self.nodes[to_node] if n != from_node]

    def adjacency_matrix(self):
        """
        Возвращает матрицу смежности в виде списка списков.
        Строки и столбцы соответствуют вершинам, отсортированным по ключу.
        """
        nodes_list = sorted(self.nodes.keys())
        n = len(nodes_list)
        matrix = [[0] * n for _ in range(n)]
        # Создаём отображение вершина -> индекс
        index_map = {node: i for i, node in enumerate(nodes_list)}

        for edge in self.edges:
            i = index_map[edge['from']]
            j = index_map[edge['to']]
            matrix[i][j] = edge['weight']
            if not self.directed:
                matrix[j][i] = edge['weight']  # симметричность для неориентированного графа

        return matrix

    def display(self):
        """Выводит матрицу смежности на экран."""
        matrix = self.adjacency_matrix()
        nodes_list = sorted(self.nodes.keys())
        print("Матрица смежности:")
        print("   ", "  ".join(str(node) for node in nodes_list))
        for i, row in enumerate(matrix):
            print(f"{nodes_list[i]}  {row}")

    def has_node(self, node):
        return node in self.nodes

    def has_edge(self, from_node, to_node):
        return self.get_edge_weight(from_node, to_node) is not None

    def get_edge_weight(self, from_node, to_node, default=None):
        """Вес ребра между вершинами или default, если ребра нет."""
        edge = self._edge_lookup.get((from_node, to_node))
        if edge is None and not self.directed:
            edge = self._edge_lookup.get((to_node, from_node))
        return edge['weight'] if edge is not None else default

    def get_neighbors(self, node):
        return self.nodes.get(node, [])

    def __str__(self):
        """Строковое представление графа (список вершин и рёбер)."""
        nodes_str = ", ".join(str(n) for n in self.nodes)
        edges_str = ", ".join(f"{e['from']}->{e['to']}({e['weight']})" for e in self.edges)
        return f"Graph(nodes=[{nodes_str}], edges=[{edges_str}])"

    @staticmethod
    def bfs(graph, start, max_depth=2):

        visited = set()
        queue = deque()

        queue.append((start, 0))
        visited.add(start)

        result = []

        while queue:
            node, depth = queue.popleft()

            if depth > 0:
                result.append(node)

            if depth >= max_depth:
                continue

            for neighbor in graph.nodes[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, depth + 1))

        return result
