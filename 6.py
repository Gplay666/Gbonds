import sys

INF = float('inf')

def dijkstra(graph, start):
    n = len(graph)
    visited = [False] * n
    dist = [INF] * n
    dist[start] = 0

    for _ in range(n):
        # выбор непосещённой вершины с минимальным расстоянием
        u = -1
        min_d = INF
        for i in range(n):
            if not visited[i] and dist[i] < min_d:
                min_d = dist[i]
                u = i
        if u == -1:
            break
        visited[u] = True

        for v in range(n):
            if graph[u][v] != INF and not visited[v]:
                new_d = dist[u] + graph[u][v]
                if new_d < dist[v]:
                    dist[v] = new_d
    return dist

def floyd_warshall(graph):
    n = len(graph)
    dist = [row[:] for row in graph]
    for k in range(n):
        for i in range(n):
            for j in range(n):
                if dist[i][k] != INF and dist[k][j] != INF:
                    if dist[i][j] > dist[i][k] + dist[k][j]:
                        dist[i][j] = dist[i][k] + dist[k][j]
    return dist

def print_matrix(matrix, cities=None):
    n = len(matrix)
    if cities is None:
        cities = [str(i) for i in range(n)]
    print("    " + "  ".join(f"{c:>3}" for c in cities))
    for i, row in enumerate(matrix):
        row_str = "  ".join(f"{x:3}" if x != INF else "INF" for x in row)
        print(f"{cities[i]:2} {row_str}")

# if __name__ == "__main__":
#     # Пример из задания (немного модифицированный)
#     graph = [
#         [0, 4, 1, INF, INF],
#         [INF, 0, INF, 2, INF],
#         [INF, 1, 0, 5, INF],
#         [INF, INF, INF, 0, 3],
#         [2, INF, INF, INF, 0]
#     ]
#     cities = ['A', 'B', 'C', 'D', 'E']
#
#     print("Исходная матрица смежности:")
#     print_matrix(graph, cities)
#
#     print("\n" + "="*60)
#     print("Результаты Дейкстры (для каждого города):")
#     for start in range(len(cities)):
#         dist = dijkstra(graph, start)
#         print(f"\nИз города {cities[start]}:")
#         for i, d in enumerate(dist):
#             print(f"  до {cities[i]}: {d if d != INF else 'нет пути'}")
#
#     print("\n" + "="*60)
#     print("Результаты Флойда-Уоршелла:")
#     fw = floyd_warshall(graph)
#     print_matrix(fw, cities)
