
from grafLib import Graph
# Создаём неориентированный граф
g = Graph(directed=False)

# Добавляем вершины
g.add_node('A')
g.add_node('B')
g.add_node('C')
g.add_node('D')

# Добавляем рёбра
g.add_edge('A', 'B', 4)
g.add_edge('A', 'C', 2)
g.add_edge('B', 'C', 1)
g.add_edge('C', 'D', 3)

print(g)
g.display()

# Удаляем вершину C
print("\nПосле удаления вершины C:")
g.remove_node('C')
print(g)
g.display()

# Пытаемся удалить несуществующее ребро
# g.remove_edge('A', 'D')  # вызовет исключение
dg = Graph(directed=True)
dg.add_node(1)
dg.add_node(2)
dg.add_node(3)
dg.add_edge(1, 2, 5)
dg.add_edge(2, 3, 7)
dg.add_edge(3, 1, 2)

print(dg)
dg.display()

dg.remove_edge(2, 3)
print("\nПосле удаления ребра 2->3:")
dg.display()
