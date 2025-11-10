#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基础走棋行为判定模块：将单步走棋唯一分类为 防守/进攻/试探 三类。
分类优先级冲突处理：进攻 > 试探 > 防守。
"""
from __future__ import annotations
from typing import Dict, Any, List, Tuple
import copy

from game.board import Board, Position, CellType
from game.piece import Player


CATEGORY_DEFEND = "defend"
CATEGORY_ATTACK = "attack"
CATEGORY_PROBE = "probe"


def classify_move(board: Board, player: Player, from_pos: Position, to_pos: Position) -> str:
    """返回走法所属类别：'attack'|'probe'|'defend'（互斥，唯一分类）。"""
    res = classify_move_ex(board, player, from_pos, to_pos)
    return str(res.get("category", CATEGORY_DEFEND))


def classify_move_ex(board: Board, player: Player, from_pos: Position, to_pos: Position) -> Dict[str, Any]:
    """返回包含类别与判定信号的结构化结果。
    signals:
      - eat_piece: 目标格存在敌方棋子（吃子）
      - enter_enemy_area_non_camp: 落子点位于敌方领地且不是行营
      - enter_center: 落子点在中央九宫格范围
      - new_exposed_unseen: 本方未暴露棋子在此步后首次暴露到敌方可直接攻击
    优先级：attack > probe > defend（防守是兜底）。
    """
    signals: Dict[str, bool] = {
        "eat_piece": False,
        "enter_enemy_area_non_camp": False,
        "enter_center": False,
        "new_exposed_unseen": False,
    }

    # 读取起止格信息
    from_cell = board.get_cell(from_pos)
    to_cell = board.get_cell(to_pos)
    if not from_cell or not from_cell.piece:
        return {"category": CATEGORY_DEFEND, "signals": signals}

    # 进攻：吃子
    if to_cell and to_cell.piece and not board._are_allied(to_cell.piece.player, from_cell.piece.player):
        signals["eat_piece"] = True

    # 进攻：进入敌方领地（不含行营）
    if to_cell and to_cell.player_area is not None and to_cell.player_area != from_cell.piece.player:
        if to_cell.cell_type != CellType.CAMP:
            signals["enter_enemy_area_non_camp"] = True

    # 试探：进入中央九宫格
    if _is_center_position(to_pos):
        signals["enter_center"] = True

    # 试探：导致未暴露本方棋子新近可被直接攻击（一步可至）
    try:
        before_unseen_exposed = _collect_attackable_unseen_positions(board, player)
        # 在副本上模拟走子（不影响原始棋盘）
        board_copy: Board = copy.deepcopy(board)
        board_copy.move_piece(from_pos, to_pos)
        after_unseen_exposed = _collect_attackable_unseen_positions(board_copy, player)
        # 若之前不可被直接攻击的未暴露棋子在此步后变为可被直接攻击，则视为“新暴露”
        newly_exposed = [pos for pos in after_unseen_exposed if pos not in before_unseen_exposed]
        if len(newly_exposed) > 0:
            signals["new_exposed_unseen"] = True
    except Exception:
        # 保守处理：模拟失败时不触发该信号
        pass

    # 类别判定（优先级处理）
    if signals["eat_piece"] or signals["enter_enemy_area_non_camp"]:
        category = CATEGORY_ATTACK
    elif signals["enter_center"] or signals["new_exposed_unseen"]:
        category = CATEGORY_PROBE
    else:
        category = CATEGORY_DEFEND

    return {"category": category, "signals": signals}


def _is_center_position(pos: Position) -> bool:
    """判断位置是否处于中央九宫格范围（行6-10，列6-10）。"""
    return 6 <= pos.row <= 10 and 6 <= pos.col <= 10


def _collect_attackable_unseen_positions(board: Board, player: Player) -> List[Position]:
    """收集当前棋盘下本方未暴露棋子中“可被敌方一步直接攻击”的位置列表。
    直接攻击依据 board.can_move(enemy_pos, my_pos) 判定（涵盖相邻与铁路规则；行营不可被攻击）。
    """
    unseen_positions: List[Position] = []
    enemy_positions: List[Position] = []

    # 枚举所有格，收集本方未暴露棋子位置与敌方棋子位置
    for pos, cell in board.cells.items():
        if not cell or not cell.piece:
            continue
        p = cell.piece
        if p.player == player and (not p.visible):
            unseen_positions.append(pos)
        elif not board._are_allied(p.player, player):
            enemy_positions.append(pos)

    # 判定是否“可被一步直接攻击”
    attackable_unseen: List[Position] = []
    for my_pos in unseen_positions:
        my_cell = board.get_cell(my_pos)
        # 行营内不可被攻击，直接跳过
        if my_cell and my_cell.cell_type == CellType.CAMP:
            continue
        # 只要存在一个敌子能一步到达，即认为此未暴露棋子当前“可被直接攻击”
        for epos in enemy_positions:
            if board.can_move(epos, my_pos):
                attackable_unseen.append(my_pos)
                break

    return attackable_unseen