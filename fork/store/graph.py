"""Read-only graph export for the dashboard and command line."""

import html


def export_json(store, app: str | None = None) -> dict:
    nodes = store.nodes(app)
    ids = {node["id"] for node in nodes}
    with store.connection() as con:
        edges = [
            dict(row)
            for row in con.execute("SELECT * FROM edges ORDER BY id")
            if row["from_node"] in ids and row["to_node"] in ids
        ]
    return {
        "nodes": [
            {
                "id": n["id"],
                "url_pattern": n["url_pattern"],
                "title": n["title_pattern"],
                "hits": n["hits"],
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": e["id"],
                "from": e["from_node"],
                "to": e["to_node"],
                "tool": e["tool"],
                "subgoal": e["subgoal"],
                "hits": e["hits"],
                "fails": e["fails"],
                "status": e["status"],
                "verification_scope": e["verification_scope"],
                "recovery_status": e["recovery_status"],
            }
            for e in edges
        ],
    }


def export_mermaid(store, app: str | None = None) -> str:
    graph = export_json(store, app)

    def label(value):
        return html.escape(str(value).replace("\n", " ")[:180], quote=True).replace(
            "|", "&#124;"
        )

    lines = ["graph LR"]
    for node in graph["nodes"]:
        lines.append(f'  {node["id"]}["{label(node["url_pattern"] or node["title"])}"]')
    for edge in graph["edges"]:
        text = label(
            f"{edge['subgoal'] or edge['tool']} ({edge['hits']}/{edge['fails']})"
        )
        arrow = "-.->" if edge["status"] != "active" else "-->"
        lines.append(f'  {edge["from"]} {arrow}|"{text}"| {edge["to"]}')
    return "\n".join(lines) + "\n"
