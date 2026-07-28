"""Seed: the Cartesian product C5 square C7."""


def construct_graph() -> dict[str, object]:
    rows = 5
    columns = 7

    def vertex(row: int, column: int) -> int:
        return (row % rows) * columns + column % columns

    edges = []
    for row in range(rows):
        for column in range(columns):
            edges.append((vertex(row, column), vertex(row, column + 1)))
            edges.append((vertex(row, column), vertex(row + 1, column)))
    return {"num_vertices": rows * columns, "edges": edges}
