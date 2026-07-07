"""Mermaid 流程图连通性校验。

脉络产出要求「全图连通、无孤立节点/分支」。本模块确定性地解析 Mermaid 代码里的
节点与边，判断无向连通性，供 engine 在写盘前把关（不通过则带反馈重试 / 报错不落盘）。

解析原则：逐行处理；先把引号内文本屏蔽掉再解析，避免把引号里的 `-->` 或标识符误判。
"""
from __future__ import annotations

import re
from typing import List, Set, Tuple

# 合法节点 id：字母/下划线开头，后接字母数字下划线
_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 引号内文本（用于屏蔽，避免把 label 里的箭头/标识符当结构解析）
_QUOTED = re.compile(r'"[^"]*"')
# 边分隔符：--> 后可紧跟 |label|
_EDGE_SPLIT = re.compile(r"-->(?:\s*\|[^|]*\|)?")
# 节点定义：ID 后紧跟 [ / ( / { （ID(("..")) 也命中，取到 ID 即可）
_NODE_DEF = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[\[({]")
# 需忽略其「行本身」的指令前缀（大小写不敏感）
_DIRECTIVE = re.compile(r"^(?:flowchart|graph|subgraph|end)\b", re.IGNORECASE)


def _mask_quotes(line: str) -> str:
    """把引号内文本替换为空引号 `""`，保留 ID[ 结构、消除引号内的箭头/标识符。"""
    return _QUOTED.sub('""', line)


def _node_from_segment(seg: str) -> str:
    """从边的一个端点片段里取节点 id：片段开头的第一个合法标识符。

    例：`A["x"]` → A、`B(("y"))` → B、`  C{d}` → C、`D` → D。
    """
    seg = seg.strip()
    m = _ID.match(seg)
    return m.group(0) if m else ""


def parse_mermaid(code: str) -> Tuple[Set[str], List[Tuple[str, str]]]:
    """解析 Mermaid 代码，返回 (节点集合, 边列表)。

    - 节点 = 边两端 id + 形如 `ID[...]`/`ID(...)`/`ID{...}`/`ID(("..."))` 的定义；
    - 边 = 含 `-->`（可带 `|label|`）的行，容忍一行多段链 `A --> B --> C`；
    - 忽略 flowchart/graph/subgraph/end 指令行本身，但 subgraph 的 id 若出现在边里则算节点。
    """
    nodes: Set[str] = set()
    edges: List[Tuple[str, str]] = []
    for raw in (code or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _DIRECTIVE.match(line):
            # 指令行本身不贡献节点（subgraph 的 id 只在它出现于边行时才算节点）
            continue
        masked = _mask_quotes(line)
        if "-->" in masked:
            segs = _EDGE_SPLIT.split(masked)
            seq = [_node_from_segment(s) for s in segs]
            for a, b in zip(seq, seq[1:]):
                if a and b:
                    edges.append((a, b))
                    nodes.add(a)
                    nodes.add(b)
        # 无论是否边行，节点定义都登记（边行里带 label 的端点也会被这里补上）
        for m in _NODE_DEF.finditer(masked):
            nodes.add(m.group(1))
    return nodes, edges


def connectivity(code: str) -> dict:
    """无向连通性检查。返回 {"ok":bool, "isolated_nodes":[...], "components":int}。

    所有出现过的节点须落在同一连通分量。解析不出任何节点或任何边时 ok=False。
    不连通时 isolated_nodes 列出【不在最大连通分量】里的节点（排序后）。
    """
    nodes, edges = parse_mermaid(code)
    if not nodes or not edges:
        return {"ok": False, "isolated_nodes": sorted(nodes), "components": 0}

    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        if a in parent and b in parent:
            union(a, b)

    comp_of = {n: find(n) for n in nodes}
    roots = set(comp_of.values())
    components = len(roots)
    if components == 1:
        return {"ok": True, "isolated_nodes": [], "components": 1}

    # 取最大连通分量为主体，其余节点视为孤立
    counts: dict = {}
    for r in comp_of.values():
        counts[r] = counts.get(r, 0) + 1
    main_root = max(counts, key=lambda r: counts[r])
    isolated = sorted(n for n in nodes if comp_of[n] != main_root)
    return {"ok": False, "isolated_nodes": isolated, "components": components}
