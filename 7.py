import random
import matplotlib.pyplot as plt
from collections import deque
import time


# ---------- Генератор лабиринта (Recursive Backtracker) ----------
def generate_maze(width=21, height=21):
    maze = [[False] * width for _ in range(height)]
    # Начальная клетка
    start_x, start_y = 1, 1
    maze[start_y][start_x] = True
    stack = [(start_x, start_y)]
    directions = [(0, -2), (0, 2), (-2, 0), (2, 0)]

    while stack:
        x, y = stack[-1]
        neighbors = []
        for dx, dy in directions:
            nx, ny = x + dx, y + dy
            if 0 < nx < width - 1 and 0 < ny < height - 1 and not maze[ny][nx]:
                neighbors.append((nx, ny, dx // 2, dy // 2))
        if neighbors:
            nx, ny, wall_dx, wall_dy = random.choice(neighbors)
            maze[y + wall_dy][x + wall_dx] = True
            maze[ny][nx] = True
            stack.append((nx, ny))
        else:
            stack.pop()
    return maze

def add_loops(maze, attempts=50):

    height = len(maze)
    width = len(maze[0])

    for _ in range(attempts):

        x = random.randrange(1, width - 1)
        y = random.randrange(1, height - 1)

        if not maze[y][x]:

            neighbors = 0

            for dx, dy in [(0,1),(0,-1),(1,0),(-1,0)]:
                nx, ny = x + dx, y + dy

                if maze[ny][nx]:
                    neighbors += 1

            if neighbors >= 2:
                maze[y][x] = True
# ---------- DFS ----------
def dfs_maze(maze, start=(1, 1), end=None):
    if end is None:
        end = (len(maze[0]) - 2, len(maze) - 2)
    height, width = len(maze), len(maze[0])
    visited = set()
    stack = [(start, [start])]
    while stack:
        pos, path = stack.pop()
        if pos in visited:
            continue
        visited.add(pos)
        if pos == end:
            return path, len(visited)
        x, y = pos
        for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and maze[ny][nx] and (nx, ny) not in visited:
                stack.append(((nx, ny), path + [(nx, ny)]))
    return None


# ---------- BFS ----------
def bfs_maze(maze, start=(1, 1), end=None):
    if end is None:
        end = (len(maze[0]) - 2, len(maze) - 2)
    height, width = len(maze), len(maze[0])
    visited = set()
    queue = deque()
    queue.append((start, [start]))
    visited.add(start)
    while queue:
        pos, path = queue.popleft()
        if pos == end:
            return path, len(visited)
        x, y = pos
        for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and maze[ny][nx] and (nx, ny) not in visited:
                visited.add((nx, ny))
                queue.append(((nx, ny), path + [(nx, ny)]))
    return None


# ---------- Визуализация ----------
def draw_maze(maze, path=None, title="Maze"):
    height, width = len(maze), len(maze[0])
    img = [[0 if not maze[y][x] else 1 for x in range(width)] for y in range(height)]
    plt.figure(figsize=(8, 8))
    plt.imshow(img, cmap='gray', interpolation='nearest')
    if path:
        path_x = [p[0] for p in path]
        path_y = [p[1] for p in path]
        plt.plot(path_x, path_y, color='red', linewidth=2)
    plt.title(title)
    plt.xticks([]), plt.yticks([])
    plt.show()


# ---------- Массовое тестирование ----------
def benchmark_algorithms(num_tests=1000, width=21, height=21):
    c=0
    total_dfs_length = 0
    total_bfs_length = 0

    total_dfs_visited = 0
    total_bfs_visited = 0

    total_dfs_time = 0
    total_bfs_time = 0

    for _ in range(num_tests):
        c+=1
        print("Итерация", c)
        maze = generate_maze(width, height)
        add_loops(maze, attempts=100)

        start = (1, 1)
        end = (width - 2, height - 2)

        # ---------- DFS ----------
        dfs_start_time = time.perf_counter()

        path_dfs, dfs_visited = dfs_maze(maze, start, end)

        dfs_end_time = time.perf_counter()

        # ---------- BFS ----------
        bfs_start_time = time.perf_counter()

        path_bfs, bfs_visited = bfs_maze(maze, start, end)

        bfs_end_time = time.perf_counter()

        # ---------- Длины путей ----------
        if path_dfs:
            total_dfs_length += len(path_dfs)

        if path_bfs:
            total_bfs_length += len(path_bfs)

        # ---------- Количество посещённых клеток ----------
        if path_dfs:
            total_dfs_visited += dfs_visited

        if path_bfs:
            total_bfs_visited += bfs_visited

        # ---------- Время ----------
        total_dfs_time += (dfs_end_time - dfs_start_time)
        total_bfs_time += (bfs_end_time - bfs_start_time)

    # ---------- Средние значения ----------
    avg_dfs_length = total_dfs_length / num_tests
    avg_bfs_length = total_bfs_length / num_tests

    avg_dfs_visited = total_dfs_visited / num_tests
    avg_bfs_visited = total_bfs_visited / num_tests

    avg_dfs_time = (total_dfs_time / num_tests) * 1000
    avg_bfs_time = (total_bfs_time / num_tests) * 1000

    # ---------- Вывод ----------
    print(f"\nTests count: {num_tests}")

    print("\n--- DFS ---")
    print(f"Average path length: {avg_dfs_length:.2f}")
    print(f"Average visited cells: {avg_dfs_visited:.2f}")
    print(f"Average execution time: {avg_dfs_time:.4f} ms")

    print("\n--- BFS ---")
    print(f"Average path length: {avg_bfs_length:.2f}")
    print(f"Average visited cells: {avg_bfs_visited:.2f}")
    print(f"Average execution time: {avg_bfs_time:.4f} ms")
# ---------- Пример ----------
# if __name__ == "__main__":
#     benchmark_algorithms()
#     W, H = 21, 21
#     maze = generate_maze(W, H)
#     add_loops(maze, attempts=100)
#     start, end = (1, 1), (W - 2, H - 2)
#
#     path_dfs, dfs_visited = dfs_maze(maze, start, end)
#     path_bfs, bfs_visited = bfs_maze(maze, start, end)
#
#     print(f"DFS path length: {len(path_dfs) if path_dfs else 'No path'}")
#     print(f"BFS path length: {len(path_bfs) if path_bfs else 'No path'}")
#
#     draw_maze(maze, path_dfs, "DFS Path")
#     draw_maze(maze, path_bfs, "BFS Path")
