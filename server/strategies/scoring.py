#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四国军棋合法走法轻量评分模块（仅使用公开信息，不泄露隐藏身份）
输出为带标签的候选走法，用于LLM侧择优。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from game.board import Board, Position, CellType
from game.piece import Piece, PieceType, Player


@dataclass
class ScoredMove:
    idx: int
    from_pos: Position
    to_pos: Position
    piece_id: Optional[str]
    score: float
    risk_level: str
    reward_level: str
    tactics: List[str]
    reason: str


def _piece_value(piece: Piece) -> int:
    # 使用已有的 get_power 作为基础价值
    return piece.get_power()


def _is_center(pos: Position) -> bool:
    # 中央九宫格行列范围（参考 board.py 的中心设置）
    return 6 <= pos.row <= 10 and 6 <= pos.col <= 10


def _positional_gain(board: Board, attacker: Piece, to_pos: Position) -> float:
    gain = 0.0
    cell = board.get_cell(to_pos)
    if not cell:
        return gain
    if _is_center(to_pos):
        gain += 1.0
    if cell.cell_type == CellType.RAILWAY:
        # 铁路枢纽连通度加分
        adj = [p for p in board.get_adjacent_positions(to_pos) if board.get_cell(p) and board.get_cell(p).cell_type == CellType.RAILWAY]
        gain += min(len(adj), 4) * 0.15
    # 敌方区域推进（进入非本方区域且非行营）
    if cell.player_area and cell.player_area != attacker.player:
        gain += 0.3
    # 行营驻守的稳健收益
    if cell.cell_type == CellType.CAMP:
        gain += 0.25
    return gain


def _exposure_risk(board: Board, attacker_player: Player, to_pos: Position) -> float:
    # 简化暴露风险：相邻敌子数量 + 铁路连通邻接度加权
    cell = board.get_cell(to_pos)
    if not cell:
        return 0.0
    # 行营免被攻击：风险大幅降低
    if cell.cell_type == CellType.CAMP:
        return 0.05
    risk = 0.0
    for adj in board.get_adjacent_positions(to_pos):
        ac = board.get_cell(adj)
        if not ac or not ac.piece:
            continue
        # 敌方单位在相邻（以行动方为参照判断敌我）
        if not board._are_allied(ac.piece.player, attacker_player):
            risk += 0.4
    # 铁路上的暴露：根据铁路邻接度估计
    if cell.cell_type == CellType.RAILWAY:
        rail_neighbors = [p for p in board.get_adjacent_positions(to_pos) if board.get_cell(p) and board.get_cell(p).cell_type == CellType.RAILWAY]
        risk += len(rail_neighbors) * 0.1
    # 归一化裁剪
    return min(risk, 1.0)


def _mobility_potential(board: Board, attacker_piece: Piece, to_pos: Position) -> float:
    # 估算下一步机动力：到达后能走的潜在步数（不做完全合法性校验，轻量近似）
    cell = board.get_cell(to_pos)
    if not cell:
        return 0.0
    # 地雷/军旗不可动（理论上不会作为移动方），保持0
    if attacker_piece.is_mine() or attacker_piece.is_flag():
        return 0.0
    if board.get_cell(to_pos).cell_type == CellType.RAILWAY:
        # 工兵连通，其他直线；用邻接铁路数做近似
        rail_neighbors = [p for p in board.get_adjacent_positions(to_pos) if board.get_cell(p) and board.get_cell(p).cell_type == CellType.RAILWAY]
        return min(len(rail_neighbors) * (1.0 if attacker_piece.is_engineer() else 0.6), 3.0) / 3.0
    else:
        adj = board.get_adjacent_positions(to_pos)
        return min(len(adj), 4) / 4.0


def _info_gain(board: Board, to_pos: Position) -> float:
    # 邻接未知敌子带来信息增益
    gain = 0.0
    for adj in board.get_adjacent_positions(to_pos):
        ac = board.get_cell(adj)
        if not ac or not ac.piece:
            continue
        if not ac.piece.visible:
            gain += 0.2
    return min(gain, 0.6)


def _defense_value(board: Board, player: Player, to_pos: Position) -> float:
    # 简化防守：靠近己方军旗区域且周边有敌子的情况下加分
    # 找到己方军旗位置（可见性不影响己方自知）
    flag_pos: Optional[Position] = None
    for pos, c in board.cells.items():
        if c.piece and c.piece.is_flag() and c.piece.player == player:
            flag_pos = pos
            break
    if not flag_pos:
        return 0.0
    # 有敌子靠近旗则防守价值上升
    enemy_near_flag = 0
    for adj in board.get_adjacent_positions(flag_pos):
        ac = board.get_cell(adj)
        if ac and ac.piece and not board._are_allied(ac.piece.player, player):
            enemy_near_flag += 1
    if enemy_near_flag == 0:
        return 0.0
    # 到旗的距离越近，防守价值越高
    dist = abs(flag_pos.row - to_pos.row) + abs(flag_pos.col - to_pos.col)
    return max(0.0, (3 - dist)) * 0.25


def _attack_ev(board: Board, attacker: Piece, to_pos: Position) -> float:
    to_cell = board.get_cell(to_pos)
    if not to_cell:
        return 0.0
    # 空格
    if not to_cell.piece:
        return 0.0
    defender = to_cell.piece
    # 行营不可被攻击（该走法应已在 can_move 中被禁止），视为0
    if to_cell.cell_type == CellType.CAMP:
        return 0.0
    # 仅在“可见”的情况下使用真实类型，否则保守估计
    if defender.visible:
        # 军旗：夺旗高收益
        if defender.is_flag():
            return 2.0
        # 地雷：非工兵进雷为巨额负分；工兵挖雷正分
        if defender.is_mine():
            return 1.2 if attacker.is_engineer() else -1.5
        # 炸弹：同归于尽，价值差分
        if defender.is_bomb():
            return (_piece_value(defender) - _piece_value(attacker)) * 0.1
        # 常规比较
        ap = _piece_value(attacker)
        dp = _piece_value(defender)
        if ap > dp:
            return (ap - dp) * 0.12
        elif ap == dp:
            return -0.05  # 互毁，略负（损失我方行动力）
        else:
            return -0.15  # 送子
    else:
        # 未知：保守估计（存在能击败我方的可能）
        # 简化为轻微负向，除非攻击方为高阶子且位置极优
        ap = _piece_value(attacker)
        base = -0.08
        if ap >= 7:  # 旅长及以上更有胜算
            base += 0.05
        return base


def _label_risk(r: float) -> str:
    if r < 0.3:
        return "low"
    if r < 0.6:
        return "medium"
    return "high"


def _label_reward(s: float, mean: float, std: float) -> str:
    threshold = mean + 0.5 * std
    if s >= threshold:
        return "high"
    if s >= mean:
        return "medium"
    return "low"


def _tactics(board: Board, attacker: Piece, from_pos: Position, to_pos: Position, attack_ev: float, pos_gain: float, def_gain: float) -> List[str]:
    tags: List[str] = []
    to_cell = board.get_cell(to_pos)
    if attack_ev > 0.15:
        tags.append("attack_win")
    elif -0.1 <= attack_ev <= 0.15 and board.get_cell(to_pos) and board.get_cell(to_pos).piece:
        tags.append("attack_trade")
    elif attack_ev < -0.1 and board.get_cell(to_pos) and board.get_cell(to_pos).piece:
        tags.append("attack_risky")
    if to_cell and to_cell.cell_type == CellType.RAILWAY:
        # 长距离（粗略：直线或连通邻居>2）
        rail_neighbors = [p for p in board.get_adjacent_positions(to_pos) if board.get_cell(p) and board.get_cell(p).cell_type == CellType.RAILWAY]
        if len(rail_neighbors) >= 3:
            tags.append("rail_sprint")
    if _is_center(to_pos):
        tags.append("central_control")
    if def_gain >= 0.3:
        tags.append("defend_flag")
    # 行营驻守
    if to_cell and to_cell.cell_type == CellType.CAMP:
        tags.append("camp_hold")
    # 轻度侦察：靠近未知敌子
    for adj in board.get_adjacent_positions(to_pos):
        ac = board.get_cell(adj)
        if ac and ac.piece and not ac.piece.visible:
            tags.append("scout")
            break
    # 位形优化
    if not board.get_cell(to_pos).piece:
        tags.append("reposition")
    return tags


def score_legal_moves(board: Board, player: Player, legal_moves: List[Tuple[Position, Position]], top_n: int = 30) -> List[Dict[str, Any]]:
    """对合法走法进行轻量评分与标签生成，并挑选约 top_n 条结果。
    仅依赖公开信息：若防守方不可见，则不使用其真实类型。
    返回结构适合直接发送给 LLM：包含 id/from/to/piece_id/score/risk_level/reward_level/tactics/reason。
    """
    scored: List[ScoredMove] = []
    for idx, (from_pos, to_pos) in enumerate(legal_moves):
        from_cell = board.get_cell(from_pos)
        if not from_cell or not from_cell.piece:
            # 非法输入，跳过
            continue
        attacker = from_cell.piece
        attack_ev = _attack_ev(board, attacker, to_pos)
        pos_gain = _positional_gain(board, attacker, to_pos)
        risk = _exposure_risk(board, attacker.player, to_pos)
        mob = _mobility_potential(board, attacker, to_pos)
        info = _info_gain(board, to_pos)
        defense = _defense_value(board, player, to_pos)
        # 权重组合
        w_attack, w_pos, w_risk, w_mob, w_info, w_def = 1.0, 0.35, 0.45, 0.25, 0.1, 0.5
        # 在旗处高威胁时提升防守权重（简单判定：旗邻有敌子）
        flag_near_enemy = defense > 0.0
        if flag_near_enemy:
            w_def = 0.8
        score = (w_attack * attack_ev +
                 w_pos * pos_gain -
                 w_risk * risk +
                 w_mob * mob +
                 w_info * info +
                 w_def * defense)
        tactics = _tactics(board, attacker, from_pos, to_pos, attack_ev, pos_gain, defense)
        reason = _build_reason(score, risk, attack_ev, tactics)
        scored.append(ScoredMove(
            idx=idx,
            from_pos=from_pos,
            to_pos=to_pos,
            piece_id=attacker.piece_id,
            score=float(round(score, 3)),
            risk_level=_label_risk(risk),
            reward_level="low",  # 占位，稍后基于分布更新
            tactics=tactics,
            reason=reason,
        ))
    # 计算 reward_level
    if scored:
        import statistics
        scores = [m.score for m in scored]
        mean = statistics.mean(scores)
        std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
        for m in scored:
            m.reward_level = _label_reward(m.score, mean, std)
    # 排序
    scored.sort(key=lambda m: m.score, reverse=True)
    # 多样性选择（简单版）：优先各战术类型的前若干条，再补齐
    selection: List[ScoredMove] = []
    quotas = {
        "attack_win": 8,
        "attack_trade": 3,
        "attack_risky": 1,
        "rail_sprint": 6,
        "central_control": 4,
        "defend_flag": 6,
        "camp_hold": 2,
        "scout": 2,
        "reposition": 2,
    }
    used = set()
    for tag, limit in quotas.items():
        for m in scored:
            if m.idx in used:
                continue
            if tag in m.tactics:
                selection.append(m)
                used.add(m.idx)
                if len([x for x in selection if tag in x.tactics]) >= limit:
                    break
            if len(selection) >= top_n:
                break
        if len(selection) >= top_n:
            break
    # 补齐到 top_n
    if len(selection) < top_n:
        for m in scored:
            if m.idx in used:
                continue
            selection.append(m)
            used.add(m.idx)
            if len(selection) >= top_n:
                break
    # 输出为字典列表
    result: List[Dict[str, Any]] = []
    for i, m in enumerate(selection):
        result.append({
            "id": i,
            "from": {"row": m.from_pos.row, "col": m.from_pos.col},
            "to": {"row": m.to_pos.row, "col": m.to_pos.col},
            "piece_id": m.piece_id,
            "score": m.score,
            "risk_level": m.risk_level,
            "reward_level": m.reward_level,
            "tactics": m.tactics,
            "reason": m.reason,
        })
    return result


def _build_reason(score: float, risk: float, attack_ev: float, tactics: List[str]) -> str:
    parts: List[str] = []
    parts.append(f"score={round(score, 2)}")
    parts.append(f"risk={_label_risk(risk)}")
    if attack_ev > 0.15:
        parts.append("吃子存活")
    elif -0.1 <= attack_ev <= 0.15:
        parts.append("可能互换")
    elif attack_ev < -0.1:
        parts.append("进攻风险")
    if tactics:
        parts.append(",".join(tactics[:2]))
    return "；".join(parts)