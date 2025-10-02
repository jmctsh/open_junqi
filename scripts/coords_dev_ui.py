#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
坐标检视开发界面（Web版）
- 复用现有棋盘数据，渲染17x17格子
- 每格显示：本地坐标(南/西/北/东 r,c) 与 全局(row,col)
- 依托 game/coords.py 的算法生成八条桥接规则涉及的源/目标全局格子并高亮

启动：python scripts/coords_dev_ui.py
将生成 dev_coords.html 并启动本地HTTP服务 http://localhost:8000/dev_coords.html
"""

import os
import http.server
import socketserver
from pathlib import Path
from typing import Dict, Tuple, List

from game.game_logic import GameLogic
from game.board import CellType, Position
from game.piece import Player
from game.coords import (
    north_edge,
    south_edge,
    west_near_center_edge,
    west_far_edge,
    east_near_center_edge,
    east_far_edge,
    row10_west_line,
    row10_east_line,
    to_global,
    from_global,
    corner_positions,
)


def _player_label(player: Player | None) -> str:
    return {
        Player.PLAYER1: "南",
        Player.PLAYER2: "西",
        Player.PLAYER3: "北",
        Player.PLAYER4: "东",
        None: "中",
    }.get(player, "?")


def build_highlight_sets() -> Dict[str, List[Tuple[int, int]]]:
    """按用户给出的八条规则准备源/目标格子集合。"""
    return {
        # 源（source）集合
        "src_south_c1": south_edge(1),
        "src_south_c5": south_edge(5),
        "src_east_c1": east_near_center_edge(),
        "src_east_c5": east_far_edge(),
        "src_north_c1": north_edge(1),
        "src_north_c5": north_edge(5),
        "src_west_c1": west_far_edge(),
        "src_west_c5": west_near_center_edge(),
        # 目标（dest）集合
        "dst_west_c5": west_near_center_edge(),
        "dst_east_c1": east_near_center_edge(),
        "dst_south_c5": south_edge(5),
        "dst_north_c1": north_edge(1),
        "dst_east_c5": east_far_edge(),
        "dst_west_c1": west_far_edge(),
        "dst_north_c5": north_edge(5),
        "dst_south_c1": south_edge(1),
        # 参考横线（南角两条）
        "line_row10_w": row10_west_line(),
        "line_row10_e": row10_east_line(),
    }


def generate_html(game: GameLogic, out_path: Path) -> None:
    board = game.board
    # 排序后的全局格子
    all_cells = sorted(board.cells.values(), key=lambda c: (c.position.row, c.position.col))

    hl = build_highlight_sets()
    corners = corner_positions()
    corner_set = set(corners.values())

    # 为快速查找构建标记：pos -> {classes}
    class_map: Dict[Tuple[int, int], List[str]] = {}
    for name, positions in hl.items():
        for r, c in positions:
            class_map.setdefault((r, c), []).append(name)
    for r, c in corner_set:
        class_map.setdefault((r, c), []).append("corner")

    # HTML/CSS 结构
    styles = r"""
    body { font-family: Arial, sans-serif; }
    .legend { margin: 10px 0; font-size: 12px; }
    .board { display: grid; grid-template-columns: repeat(17, 36px); grid-template-rows: repeat(17, 36px); gap: 4px; }
    .cell { position: relative; border: 1px solid #999; background: #f4f4f4; }
    .cell.railway { background: rgba(173,216,230,0.6); }
    .cell.camp { background: rgba(255,255,0,0.5); }
    .cell.hq { background: rgba(255,0,0,0.5); }
    .cell.south { background: rgba(255,200,200,0.25); }
    .cell.west { background: rgba(200,255,200,0.25); }
    .cell.north { background: rgba(200,200,255,0.25); }
    .cell.east { background: rgba(255,255,200,0.25); }
    .txt-local { position:absolute; left:2px; top:2px; font-size:10px; color:#000; }
    .txt-global { position:absolute; left:2px; bottom:2px; font-size:10px; color:#007; }
    /* 高亮 */
    .hl-src { outline: 2px solid #2ecc71; }
    .hl-dst { outline: 2px solid #e67e22; }
    .corner { outline: 2px dashed #8e44ad; }
    """

    def cell_classes(cell) -> List[str]:
        classes: List[str] = ["cell"]
        # 类型
        if cell.cell_type == CellType.RAILWAY:
            classes.append("railway")
        elif cell.cell_type == CellType.CAMP:
            classes.append("camp")
        elif cell.cell_type == CellType.HEADQUARTERS:
            classes.append("hq")
        # 区域
        area = cell.player_area
        if area == Player.PLAYER1:
            classes.append("south")
        elif area == Player.PLAYER2:
            classes.append("west")
        elif area == Player.PLAYER3:
            classes.append("north")
        elif area == Player.PLAYER4:
            classes.append("east")

        # 源/目标高亮
        pos = (cell.position.row, cell.position.col)
        tags = class_map.get(pos, [])
        if any(t.startswith("src_") for t in tags):
            classes.append("hl-src")
        if any(t.startswith("dst_") for t in tags):
            classes.append("hl-dst")
        if "corner" in tags:
            classes.append("corner")
        return classes

    # 单元格内容：本地与全局坐标
    def cell_texts(cell) -> Tuple[str, str]:
        area = cell.player_area
        label = _player_label(area)
        r, c = cell.position.row, cell.position.col
        # 中央区域显示“中x,y”（5x5九宫格只显示奇数位，这里直接显示全局坐标）
        if area is None:
            local_text = f"中"
        else:
            lr, lc = from_global(area, r, c)
            local_text = f"{label}{lr},{lc}"
        global_text = f"({r},{c})"
        return local_text, global_text

    # 生成HTML
    html_cells: List[str] = []
    for cell in all_cells:
        r, c = cell.position.row, cell.position.col
        classes = " ".join(cell_classes(cell))
        local_text, global_text = cell_texts(cell)
        html_cells.append(
            f'<div class="{classes}" style="grid-row:{r+1};grid-column:{c+1};">'
            f'<div class="txt-local">{local_text}</div>'
            f'<div class="txt-global">{global_text}</div>'
            f'</div>'
        )

    legend = (
        "<div class=legend>"
        "颜色说明：绿色边=桥接源五点；橙色边=桥接目标五点；紫色虚框=四侧角点。"
        "</div>"
    )

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{styles}</style></head><body>"
        "<h3>坐标检视开发界面</h3>" + legend + "<div class='board'>"
        + "".join(html_cells) + "</div>"
        "</body></html>"
    )

    out_path.write_text(html, encoding="utf-8")


def main():
    game = GameLogic()
    out_file = Path("dev_coords.html")
    generate_html(game, out_file)

    # 启动静态HTTP服务
    port = 8000
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), Handler) as httpd:
        print(f"[coords-dev] Serving at http://localhost:{port}/dev_coords.html")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("Stop server.")


if __name__ == "__main__":
    main()