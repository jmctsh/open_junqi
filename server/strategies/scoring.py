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
from server.strategies.behaviors import (
    classify_move,
    CATEGORY_ATTACK,
    CATEGORY_PROBE,
    CATEGORY_DEFEND,
)
# 延迟导入搜索模块以避免循环依赖
from game.history import HistoryRecorder, MoveRecord
import random
from typing import Set


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


def _is_revealed_high(piece: Piece) -> bool:
    """根据对战推断的“明司令/明军长”判定：
    满足：棋子为司令或军长，且已有击杀（kill_count>0），
    代表其身份已基本被对手推断为大子。与 UI 的 piece.visible 无关。
    """
    try:
        return piece.piece_type in (PieceType.COMMANDER, PieceType.GENERAL) and getattr(piece, "kill_count", 0) > 0
    except Exception:
        return False


def _is_fake_commander(piece: Piece) -> bool:
    """假司令判定：非司/军的中等子（师/旅/团）但已有击杀。
    这类子可被对手高估并产生威慑效果，常用于伪装航母编队控场。
    """
    try:
        return piece.piece_type in (PieceType.DIVISION, PieceType.BRIGADE, PieceType.REGIMENT) and getattr(piece, "kill_count", 0) > 0
    except Exception:
        return False


def _is_center(pos: Position) -> bool:
    # 中央九宫格行列范围（参考 board.py 的中心设置）
    return 6 <= pos.row <= 10 and 6 <= pos.col <= 10


def _alive_engineer_count(board: Board, ref_player: Player) -> int:
    cnt = 0
    for _, cell in board.cells.items():
        if not cell or not cell.piece:
            continue
        if cell.piece.player == ref_player and cell.piece.piece_type == PieceType.ENGINEER:
            cnt += 1
    return cnt


def _enemy_flag_positions(board: Board, ref_player: Player) -> List[Position]:
    flags: List[Position] = []
    for pos, cell in board.cells.items():
        if cell and cell.piece and cell.piece.is_flag() and (not board._are_allied(cell.piece.player, ref_player)):
            flags.append(pos)
    return flags


def _is_near_enemy_flag(board: Board, ref_player: Player, pos: Position, radius: int = 2) -> bool:
    flags = _enemy_flag_positions(board, ref_player)
    return any(abs(pos.row - fp.row) + abs(pos.col - fp.col) <= radius for fp in flags)


def _is_suspected_mine_target(board: Board, ref_player: Player, target_pos: Position) -> bool:
    """未知目标是否疑似地雷（限定为“军旗上方/侧角点”的硬坐标）。"""
    cell = board.get_cell(target_pos)
    if not cell or not cell.piece:
        return False
    if cell.piece.visible:
        return False
    if board._are_allied(cell.piece.player, ref_player):
        return False
    probe_points = _flag_probe_points(board, ref_player)
    return any((target_pos.row == p.row and target_pos.col == p.col) for p in probe_points)


def _is_suspected_bomb_target(board: Board, ref_player: Player, target_pos: Position) -> bool:
    """未知目标是否疑似炸弹（常见于“师长+炸弹”的后方暗子）。"""
    cell = board.get_cell(target_pos)
    if not cell or not cell.piece:
        return False
    if cell.piece.visible:
        return False
    if board._are_allied(cell.piece.player, ref_player):
        return False
    # 开局先验1：炸弹不能在第一排（按防守方本地坐标）
    try:
        lr, lc = _get_local_coords(target_pos, cell.piece.player)
        if lr == 1:
            return False
    except Exception:
        pass
    # 开局先验2：本地行营(2,2)/(2,4)与军旗上方行营(4,2)/(4,4)更可能是炸弹
    try:
        lr, lc = _get_local_coords(target_pos, cell.piece.player)
        if (lr, lc) in ((2, 2), (2, 4), (4, 2), (4, 4)):
            return True
    except Exception:
        pass
    # 同步依据军旗推导出的“旗上方”点（若防守方总部已识别），也视为疑似炸弹点
    try:
        probe_points = _flag_probe_points(board, ref_player)
        if any((target_pos.row == p.row and target_pos.col == p.col) for p in probe_points):
            # 仅当该点属于防守方本地阵营
            if cell.player_area and not board._are_allied(cell.player_area, ref_player):
                return True
    except Exception:
        pass
    # 邻接已可见的敌方师长
    for adj in board.get_adjacent_positions(target_pos):
        ac = board.get_cell(adj)
        if ac and ac.piece and (not board._are_allied(ac.piece.player, ref_player)) and ac.piece.visible:
            # 扩展：任意已显露高子（≥旅）邻接的暗子更可疑
            try:
                if _piece_value(ac.piece) >= 7:
                    return True
            except Exception:
                # 兜底：明确类型判定
                if ac.piece.piece_type in (PieceType.DIVISION, PieceType.BRIGADE, PieceType.REGIMENT, PieceType.GENERAL, PieceType.COMMANDER):
                    return True
    return False


# === 阵地/行营/前排与补位判定 ===

def _get_local_coords(pos: Position, player: Player) -> Tuple[int, int]:
    """复制 game_logic._get_local_coords 以便评分层判定本地行列。"""
    if player == Player.PLAYER1:  # 南方：6x5，起点(11,6)
        local_row = pos.row - 11 + 1
        local_col = pos.col - 6 + 1
    elif player == Player.PLAYER2:  # 西方：5x6，逆时针90°
        global_row_in_area = pos.row - 6
        global_col_in_area = pos.col - 0
        local_row = 5 - global_col_in_area + 1
        local_col = global_row_in_area + 1
    elif player == Player.PLAYER3:  # 北方：6x5，旋转180°
        global_row_in_area = pos.row - 0
        global_col_in_area = pos.col - 6
        local_row = 6 - global_row_in_area
        local_col = 5 - global_col_in_area
    else:  # Player.PLAYER4 东方：5x6，顺时针90°
        global_row_in_area = pos.row - 6
        global_col_in_area = pos.col - 11
        local_row = global_col_in_area + 1
        local_col = 5 - global_row_in_area
    return int(local_row), int(local_col)


def _is_first_row(board: Board, player: Player, pos: Position) -> bool:
    cell = board.get_cell(pos)
    if not cell or cell.player_area != player:
        return False
    lr, _ = _get_local_coords(pos, player)
    return lr == 1

def _is_last_two_rows(board: Board, player: Player, pos: Position) -> bool:
    """是否为本方阵营的后两排（本地行5、6）。"""
    cell = board.get_cell(pos)
    if not cell or cell.player_area != player:
        return False
    lr, _ = _get_local_coords(pos, player)
    return lr in (5, 6)
 
def _local_to_global(player: Player, local_row: int, local_col: int) -> Position:
    """将本地坐标(r,c)转换为全局(row,col)，行1靠近九宫格，行6远离九宫格。"""
    if player == Player.PLAYER1:
        return Position(10 + local_row, 5 + local_col)
    if player == Player.PLAYER3:
        return Position(6 - local_row, 11 - local_col)
    if player == Player.PLAYER2:
        return Position(5 + local_col, 6 - local_row)
    return Position(11 - local_col, 10 + local_row)

def _enemy_hq_positions(board: Board, ref_player: Player) -> List[Position]:
    """收集所有非同盟阵营的大本营格子。"""
    hqs: List[Position] = []
    for pos, cell in board.cells.items():
        if not cell:
            continue
        if cell.cell_type == CellType.HEADQUARTERS and (cell.player_area is not None) and (not board._are_allied(cell.player_area, ref_player)):
            hqs.append(pos)
    return hqs

def _flag_probe_points(board: Board, ref_player: Player) -> List[Position]:
    """根据敌方大本营计算“军旗上方/侧角点”全局坐标集合。"""
    points: List[Position] = []
    for hq_pos in _enemy_hq_positions(board, ref_player):
        cell = board.get_cell(hq_pos)
        if not cell or not cell.player_area:
            continue
        lr, lc = _get_local_coords(hq_pos, cell.player_area)
        if lr == 6 and lc in (2, 4):
            # 更正：军旗上方的行营在本地第4行（不是第5行）
            top_local = (4, lc)
            side_local = (5, 1 if lc == 2 else 5)
            points.append(_local_to_global(cell.player_area, top_local[0], top_local[1]))
            points.append(_local_to_global(cell.player_area, side_local[0], side_local[1]))
    return points


def _count_adjacent_allied_bombs(board: Board, player: Player, pos: Position) -> int:
    cnt = 0
    for adj in board.get_adjacent_positions(pos):
        ac = board.get_cell(adj)
        if ac and ac.piece and ac.piece.player == player and ac.piece.is_bomb():
            cnt += 1
    return cnt


def _count_adjacent_allied_unrevealed_nonbomb(board: Board, player: Player, pos: Position) -> int:
    cnt = 0
    for adj in board.get_adjacent_positions(pos):
        ac = board.get_cell(adj)
        if ac and ac.piece and ac.piece.player == player and (not ac.piece.is_bomb()) and (not ac.piece.visible):
            cnt += 1
    return cnt


def _count_adjacent_allied_high(board: Board, player: Player, pos: Position) -> int:
    cnt = 0
    for adj in board.get_adjacent_positions(pos):
        ac = board.get_cell(adj)
        if ac and ac.piece and ac.piece.player == player and ac.piece.piece_type in (PieceType.DIVISION, PieceType.BRIGADE, PieceType.REGIMENT, PieceType.GENERAL, PieceType.COMMANDER):
            cnt += 1
    return cnt


def _friendly_hq_positions(board: Board, player: Player) -> List[Position]:
    hqs: List[Position] = []
    for pos, cell in board.cells.items():
        if cell and cell.cell_type == CellType.HEADQUARTERS and cell.player_area == player:
            hqs.append(pos)
    return hqs


def _unknown_enemies_near(board: Board, player: Player, positions: List[Position], radius: int = 2) -> bool:
    for fp in positions:
        for pos, cell in board.cells.items():
            if not cell or not cell.piece:
                continue
            if board._are_allied(cell.piece.player, player):
                continue
            if cell.piece.visible:
                continue
            if abs(pos.row - fp.row) + abs(pos.col - fp.col) <= radius:
                return True
    return False


def _positional_gain(board: Board, attacker: Piece, to_pos: Position) -> float:
    gain = 0.0
    cell = board.get_cell(to_pos)
    if not cell:
        return gain
    if _is_center(to_pos):
        gain += 0.8  # 降低中心加成，避免过度偏好
    if cell.cell_type == CellType.RAILWAY:
        # 铁路枢纽连通度加分
        adj = [p for p in board.get_adjacent_positions(to_pos) if board.get_cell(p) and board.get_cell(p).cell_type == CellType.RAILWAY]
        gain += min(len(adj), 4) * 0.15
    # 敌方区域推进（进入非本方区域且非行营）
    if cell.player_area and cell.player_area != attacker.player:
        gain += 0.4  # 适度提高推进奖励，鼓励进攻与压制
    # 行营驻守的稳健收益
    if cell.cell_type == CellType.CAMP:
        gain += 0.35  # 强化行营驻守
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


def _alive_highpiece_counts(board: Board, ref_player: Player) -> Tuple[int, int, int, int]:
    """返回 (盟友司令数, 盟友军长数, 敌方司令数, 敌方军长数) 当前存活的数量。"""
    allies: List[Player] = []
    enemies: List[Player] = []
    for p in [Player.PLAYER1, Player.PLAYER2, Player.PLAYER3, Player.PLAYER4]:
        try:
            if board._are_allied(p, ref_player):
                allies.append(p)
            else:
                enemies.append(p)
        except Exception:
            # 简单轴规则兜底：1/3为盟，2/4为盟
            if (p in (Player.PLAYER1, Player.PLAYER3) and ref_player in (Player.PLAYER1, Player.PLAYER3)) or (
                p in (Player.PLAYER2, Player.PLAYER4) and ref_player in (Player.PLAYER2, Player.PLAYER4)
            ):
                allies.append(p)
            else:
                enemies.append(p)
    acmd = agen = ecmd = egen = 0
    for pos, cell in board.cells.items():
        if not cell or not cell.piece:
            continue
        pt = cell.piece.piece_type
        pl = cell.piece.player
        if pl in allies:
            if pt == PieceType.COMMANDER:
                acmd += 1
            elif pt == PieceType.GENERAL:
                agen += 1
        else:
            if pt == PieceType.COMMANDER:
                ecmd += 1
            elif pt == PieceType.GENERAL:
                egen += 1
    return acmd, agen, ecmd, egen


def _attack_ev(board: Board, attacker: Piece, to_pos: Position, player: Player, history: Optional[HistoryRecorder] = None) -> float:
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
    # 仅在“可见”的情况下使用真实类型，否则按启发式估计，并考虑“情报价值”
    if defender.visible:
        # 军旗：夺旗高收益（细化：司/军在非终局时降低收益，特殊情况提升）
        if defender.is_flag():
            val = 2.0
            if attacker.piece_type in (PieceType.COMMANDER, PieceType.GENERAL):
                # 判断是否为最后一个敌方玩家（夺旗即终局）
                enemies = [p for p in [Player.PLAYER1, Player.PLAYER2, Player.PLAYER3, Player.PLAYER4] if not board._are_allied(p, player)]
                other_enemies = [p for p in enemies if p != defender.player]
                other_alive = False
                for pos2, cell2 in board.cells.items():
                    if not cell2 or not cell2.piece:
                        continue
                    if cell2.piece.player in other_enemies:
                        other_alive = True
                        break
                if other_alive:
                    # 非终局：司/军夺旗保守
                    val -= 0.6
                    # 若该玩家司令仍存活，夺旗等同击杀其司令，提升
                    flag_side_cmd_alive = any(
                        (c and c.piece and c.piece.player == defender.player and c.piece.piece_type == PieceType.COMMANDER)
                        for _, c in board.cells.items()
                    )
                    if flag_side_cmd_alive:
                        val += 0.7
                    # 若敌方无司/军存活（我方司令为场上最大），进一步降低
                    _, _, ecmd, egen = _alive_highpiece_counts(board, player)
                    if ecmd == 0 and egen == 0:
                        val -= 0.4
                else:
                    # 终局：夺旗即胜，强烈鼓励
                    val += 0.8
            return val
        # 地雷：非工兵进雷为巨额负分；工兵挖雷正分
        if defender.is_mine():
            return 1.2 if attacker.is_engineer() else -1.5
        # 炸弹：同归于尽，价值差分
        if defender.is_bomb():
            # 司令/军长主动撞炸弹：更强的负面；否则按原差分
            if attacker.piece_type in (PieceType.COMMANDER, PieceType.GENERAL):
                return -0.6
            return (_piece_value(defender) - _piece_value(attacker)) * 0.1
        # 攻击方为炸弹：优先击杀高价值子（同归于尽）
        if attacker.is_bomb():
            # 夺旗：极高收益
            if defender.is_flag():
                return 2.2
            # 炸地雷通常无意义
            if defender.is_mine():
                return -0.8
            # 高价值子强烈加成
            if defender.piece_type == PieceType.COMMANDER:
                return 2.0
            if defender.piece_type == PieceType.GENERAL:
                return 1.6
            if defender.piece_type == PieceType.DIVISION:
                return 1.1
            if defender.piece_type == PieceType.BRIGADE:
                return 0.9
            if defender.piece_type == PieceType.REGIMENT:
                return 0.7
            # 中低价值：谨慎（避免浪费炸弹）
            if _piece_value(defender) <= 4:  # 连/排及更小
                return -0.3
            # 营：轻微负分，通常不值得
            return -0.15
        # 常规比较
        ap = _piece_value(attacker)
        dp = _piece_value(defender)
        # 特例：高阶子（司令/军长）之间的打兑与克杀
        if attacker.piece_type == PieceType.COMMANDER and defender.piece_type == PieceType.COMMANDER:
            # 司令兑司令：根据明暗与双方高子存活优势调整
            acmd, agen, ecmd, egen = _alive_highpiece_counts(board, player)
            advantage = (acmd + 0.5 * agen) - (ecmd + 0.5 * egen)
            att_rev = _is_revealed_high(attacker)
            def_rev = _is_revealed_high(defender)
            if att_rev and not def_rev:
                bonus = 0.6 + 0.15 * advantage
                # “明司令”若已击杀过子，偏好进一步换掉暗司令
                if getattr(attacker, "kill_count", 0) > 0:
                    bonus += 0.08
                return bonus
            if att_rev and def_rev:
                return 0.2 + 0.15 * advantage
            if (not att_rev) and def_rev:
                return -0.4 + 0.1 * advantage
            # 都是暗：略负或略正随优势
            return 0.0 + 0.1 * advantage
        if attacker.piece_type == PieceType.GENERAL and defender.piece_type == PieceType.COMMANDER:
            # 军长撞司令：强负分，几乎不可接受
            return -1.2
        if attacker.piece_type == PieceType.COMMANDER and defender.piece_type == PieceType.GENERAL:
            # 司令吃军长：高回报（即便可能被炸），提升正分
            return 0.7
        if attacker.piece_type == PieceType.GENERAL and defender.piece_type == PieceType.DIVISION:
            # 在我方司令未阵亡前，军长吃师长非常有利（削弱敌方对司令的威胁链）
            # 检查我方司令是否存活
            acmd, _, _, _ = _alive_highpiece_counts(board, player)
            if acmd > 0:
                return 0.35
            return 0.25
        # 其他常规比较
        if ap > dp:
            return (ap - dp) * 0.12
        elif ap == dp:
            # 高阶子之间的打兑（军长对军长）按明暗偏好
            if attacker.piece_type == PieceType.GENERAL and defender.piece_type == PieceType.GENERAL:
                acmd, agen, ecmd, egen = _alive_highpiece_counts(board, player)
                advantage = (acmd + 0.5 * agen) - (ecmd + 0.5 * egen)
                att_rev = _is_revealed_high(attacker)
                def_rev = _is_revealed_high(defender)
                if att_rev and not def_rev:
                    bonus = 0.4 + 0.12 * advantage
                    if getattr(attacker, "kill_count", 0) > 0:
                        bonus += 0.05
                    return bonus
                if att_rev and def_rev:
                    return 0.1 + 0.12 * advantage
                if (not att_rev) and def_rev:
                    return -0.3 + 0.1 * advantage
                return 0.0 + 0.1 * advantage
            return -0.05  # 互毁，略负（损失我方行动力）
        else:
            return -0.15  # 送子
    else:
        # 未知：根据攻击方类型引入“情报价值”与试探偏好
        ap = _piece_value(attacker)
        base = -0.02
        # 大子试探略正，符合“伺机而动、试探未知强子”的策略
        if ap >= 7:  # 旅长及以上更有胜算
            base += 0.08
        if ap >= 9:  # 师长及以上进一步放宽
            base += 0.04
        # 使用首个师长换取敌方军长/司令情报：勉强为正
        if attacker.piece_type == PieceType.DIVISION:
            base += 0.06
        # 司令/军长贸然进攻未知单位：保守，略减分
        if attacker.piece_type in (PieceType.COMMANDER, PieceType.GENERAL):
            base -= 0.08
        # 工兵位先验：前线(1,2)/(1,3)/(1,4)常见工兵——鼓励旅/团去试探
        try:
            _cell = board.get_cell(to_pos)
            if _cell and _cell.piece and (not _cell.piece.visible) and (not board._are_allied(_cell.piece.player, player)):
                lr_e, lc_e = _get_local_coords(to_pos, _cell.piece.player)
                if lr_e == 1 and lc_e in (2, 3, 4) and attacker.piece_type in (PieceType.BRIGADE, PieceType.REGIMENT):
                    base += 0.16
        except Exception:
            pass
        # 开局先验3：前线(1,1)/(1,3)/(1,5)未知更可能是大子——小子进攻更保守
        try:
            to_cell_u = board.get_cell(to_pos)
            if to_cell_u and to_cell_u.piece and (not to_cell_u.piece.visible) and (not board._are_allied(to_cell_u.piece.player, player)):
                lr_u, lc_u = _get_local_coords(to_pos, to_cell_u.piece.player)
                if lr_u == 1 and lc_u in (1, 3, 5):
                    if _piece_value(attacker) <= 6:
                        base -= 0.12
                    else:
                        base -= 0.03
        except Exception:
            pass
        # 后两排未知：鼓励旅/团进攻，司/军更保守（地雷概率高）
        try:
            _cell2 = board.get_cell(to_pos)
            if _cell2 and _cell2.piece and (not _cell2.piece.visible) and (not board._are_allied(_cell2.piece.player, player)):
                if _is_last_two_rows(board, _cell2.piece.player, to_pos):
                    if attacker.piece_type in (PieceType.BRIGADE, PieceType.REGIMENT):
                        base += 0.22
                    if attacker.piece_type in (PieceType.COMMANDER, PieceType.GENERAL):
                        base -= 0.10
        except Exception:
            pass
        # 假司令（师/旅/团且已有击杀）试探未知：更积极，中心进一步加成
        if _is_fake_commander(attacker):
            kc = getattr(attacker, "kill_count", 0)
            base += 0.12 + min(kc, 2) * 0.04
            if _is_center(to_pos):
                base += 0.08
        # 工兵未知攻击：仅在“敌旗近地雷”或“师长后疑炸弹”时鼓励；否则惩罚
        to_cell = board.get_cell(to_pos)
        if attacker.piece_type == PieceType.ENGINEER and to_cell and to_cell.piece and (not to_cell.piece.visible):
            eng_cnt = _alive_engineer_count(board, player)
            if _is_suspected_mine_target(board, player, to_pos):
                base += 0.28
                if eng_cnt <= 2:
                    base -= 0.08
                if eng_cnt <= 1:
                    base -= 0.12
            elif _is_suspected_bomb_target(board, player, to_pos):
                base += 0.16
                if eng_cnt <= 2:
                    base -= 0.08
                if eng_cnt <= 1:
                    base -= 0.12
            else:
                base -= 0.18
        # 若敌方司令均已阵亡，军长吃未知总体更积极（尤其中心）
        if attacker.piece_type == PieceType.GENERAL:
            _, _, ecmd, _ = _alive_highpiece_counts(board, player)
            if ecmd == 0:
                base += 0.25
                if _is_center(to_pos):
                    base += 0.1
        # 师长在中心抢占未知：提高情报/控场价值
        if attacker.piece_type == PieceType.DIVISION and _is_center(to_pos):
            base += 0.08
        # 旅/团在中心对未知目标：视为“控场争夺”，加成更高
        to_cell = board.get_cell(to_pos)
        if attacker.piece_type in (PieceType.BRIGADE, PieceType.REGIMENT) and to_cell and to_cell.piece and (not to_cell.piece.visible) and _is_center(to_pos):
            base += 0.22
        # 敌方司令均亡时，司令在中心对未知可适度放宽保守
        if attacker.piece_type == PieceType.COMMANDER:
            _, _, ecmd, _ = _alive_highpiece_counts(board, player)
            if ecmd == 0 and _is_center(to_pos):
                base += 0.08
        # 小子主动“摸雷”与中央试探：更鼓励（营/连/排）
        if attacker.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON):
            base += 0.14
            # 若目标靠近我方司/军（可能伪装炸弹威胁），加成更高
            # 扫描本方司/军位置
            carriers: List[Position] = []
            for pos, cell in board.cells.items():
                if not cell or not cell.piece:
                    continue
                p = cell.piece
                if p.player == player and (p.piece_type in (PieceType.COMMANDER, PieceType.GENERAL) or _is_fake_commander(p)):
                    carriers.append(pos)
            if any(abs(to_pos.row - cp.row) + abs(to_pos.col - cp.col) <= 2 for cp in carriers):
                base += 0.15
            # 中央九宫格的试探，便于吸引敌方大子出击并排除炸弹
            if _is_center(to_pos):
                base += 0.1
            # 师长后疑炸弹：允许小子试探以获取情报
            to_cell2 = board.get_cell(to_pos)
            if to_cell2 and to_cell2.piece and (not to_cell2.piece.visible) and _is_suspected_bomb_target(board, player, to_pos):
                base += 0.1
        # 历史先验：最近吃掉我方子的“入侵者”更可能是大子——鼓励旅/团/师去吃；军长在敌司明确或亡时额外鼓励
        try:
            to_cell_hist = board.get_cell(to_pos)
            if history and to_cell_hist and to_cell_hist.piece and (not to_cell_hist.piece.visible):
                pid = getattr(to_cell_hist.piece, "piece_id", None)
                invaders: List[str] = _find_counterattack_targets(history, player) if pid else []
                if pid and pid in invaders:
                    if attacker.piece_type in (PieceType.BRIGADE, PieceType.REGIMENT, PieceType.DIVISION):
                        base += 0.28
                    if attacker.piece_type == PieceType.GENERAL:
                        # 敌司令可见或已亡
                        _, _, ecmd, _ = _alive_highpiece_counts(board, player)
                        enemy_cmd_visible = any(
                            (c and c.piece and (not board._are_allied(c.piece.player, player)) and c.piece.piece_type == PieceType.COMMANDER and c.piece.visible)
                            for _, c in board.cells.items()
                        )
                        if enemy_cmd_visible or ecmd == 0:
                            base += 0.22
                    # 我方仅剩炸弹时，用炸弹炸入侵者优于放跑
                    if attacker.is_bomb():
                        acmd, agen, _, _ = _alive_highpiece_counts(board, player)
                        ally_div = sum(1 for _, c in board.cells.items() if c and c.piece and c.piece.player == player and c.piece.piece_type == PieceType.DIVISION)
                        if (acmd + agen + ally_div) == 0:
                            base += 0.24
                # 频繁进攻/活跃试探的敌子：提升其为大子的先验，旅/团/师获取情报更有价值
                atk_cnt = 0
                mv_cnt = 0
                for rec in history.records[-50:]:
                    if getattr(rec, "piece_id", None) == pid:
                        oc = getattr(rec, "outcome", "move")
                        if isinstance(oc, str) and oc.startswith("attack_"):
                            atk_cnt += 1
                        elif oc == "move":
                            mv_cnt += 1
                aggressive = (atk_cnt >= 2) or (atk_cnt >= 1 and mv_cnt >= 3) or (mv_cnt >= 5)
                if aggressive:
                    base += 0.14
                    if attacker.piece_type in (PieceType.BRIGADE, PieceType.REGIMENT, PieceType.DIVISION):
                        base += 0.10
        except Exception:
            pass
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
        # 小子中央布子（非吃子）作为试探
        if attacker.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON) and not (to_cell and to_cell.piece):
            tags.append("central_probe")
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
    # 小子“摸雷”试探：攻击未知单位
    if attacker.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON) and to_cell and to_cell.piece and (not to_cell.piece.visible):
        tags.append("bomb_probe")
        # 师长后疑炸弹：小子试探
        if _is_suspected_bomb_target(board, attacker.player, to_pos):
            tags.append("bomb_suspect_probe")
    # 工兵高价值试探：敌旗近地雷与师长后疑炸弹
    if attacker.piece_type == PieceType.ENGINEER and to_cell and to_cell.piece and (not to_cell.piece.visible):
        if _is_suspected_mine_target(board, attacker.player, to_pos):
            tags.append("engineer_flag_probe")
        if _is_suspected_bomb_target(board, attacker.player, to_pos):
            tags.append("bomb_suspect_probe")
    # 护航：小子靠近司/军
    carriers: List[Position] = []
    for pos, cell in board.cells.items():
        if not cell or not cell.piece:
            continue
        p = cell.piece
        if p.player == attacker.player and p.piece_type in (PieceType.COMMANDER, PieceType.GENERAL):
            carriers.append(pos)
    if attacker.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON) and any(abs(to_pos.row - cp.row) + abs(to_pos.col - cp.col) == 1 for cp in carriers):
        tags.append("escort")
    # 护航：小子靠近假司令（师/旅/团且有击杀）
    fake_carriers: List[Position] = []
    for pos, cell in board.cells.items():
        if not cell or not cell.piece:
            continue
        p = cell.piece
        if p.player == attacker.player and _is_fake_commander(p):
            fake_carriers.append(pos)
    if attacker.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON) and any(abs(to_pos.row - cp.row) + abs(to_pos.col - cp.col) == 1 for cp in fake_carriers):
        tags.append("escort_fake")
    # 炸弹掩护：炸弹贴身高子
    if attacker.is_bomb():
        for adj in board.get_adjacent_positions(to_pos):
            ac = board.get_cell(adj)
            if ac and ac.piece and ac.piece.player == attacker.player and ac.piece.piece_type in (PieceType.DIVISION, PieceType.BRIGADE, PieceType.REGIMENT, PieceType.GENERAL, PieceType.COMMANDER):
                tags.append("bomb_cover")
                break
    # 炸弹陷阱：高子到位且相邻有本方炸弹
    if attacker.piece_type in (PieceType.DIVISION, PieceType.BRIGADE, PieceType.REGIMENT, PieceType.GENERAL, PieceType.COMMANDER):
        for adj in board.get_adjacent_positions(to_pos):
            ac = board.get_cell(adj)
            if ac and ac.piece and ac.piece.player == attacker.player and ac.piece.is_bomb():
                tags.append("bomb_trap")
                # 若该炸弹此前没有邻接任何本方高子，视为“陷阱补位”
                if _count_adjacent_allied_high(board, attacker.player, adj) == 0:
                    tags.append("trap_rebuild")
                break
    # 假炸弹佯攻：未暴露的非炸弹贴身高子
    if (not attacker.is_bomb()) and hasattr(attacker, "visible") and (not attacker.visible):
        for adj in board.get_adjacent_positions(to_pos):
            ac = board.get_cell(adj)
            if ac and ac.piece and ac.piece.player == attacker.player and ac.piece.piece_type in (PieceType.DIVISION, PieceType.BRIGADE, PieceType.REGIMENT, PieceType.GENERAL, PieceType.COMMANDER):
                tags.append("bomb_feint")
                # 若该高子当前没有任何未暴露的非炸弹相邻，则视为“假炸弹补位”
                if _count_adjacent_allied_unrevealed_nonbomb(board, attacker.player, adj) == 0:
                    tags.append("feint_refill")
                break
    # 司/军护航状态标签
    if attacker.piece_type in (PieceType.COMMANDER, PieceType.GENERAL):
        small_adj = 0
        for adj in board.get_adjacent_positions(to_pos):
            ac = board.get_cell(adj)
            if ac and ac.piece and ac.piece.player == attacker.player and ac.piece.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON):
                small_adj += 1
        if small_adj >= 2:
            tags.append("well_guarded")
        elif small_adj == 0:
            tags.append("isolated_carrier")
    # 假司令护航状态标签
    if _is_fake_commander(attacker):
        small_adj = 0
        for adj in board.get_adjacent_positions(to_pos):
            ac = board.get_cell(adj)
            if ac and ac.piece and ac.piece.player == attacker.player and ac.piece.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON):
                small_adj += 1
        if small_adj >= 2:
            tags.append("fake_carrier")
    # 位形优化
    if not board.get_cell(to_pos).piece:
        tags.append("reposition")
    # 炸弹向军旗附近转移（在军旗附近有未知敌子压力时）
    if attacker.is_bomb():
        hqs = _friendly_hq_positions(board, attacker.player)
        if hqs and _unknown_enemies_near(board, attacker.player, hqs, radius=2):
            nearest_hq_dist = min(abs(to_pos.row - hq.row) + abs(to_pos.col - hq.col) for hq in hqs)
            if nearest_hq_dist <= 2:
                tags.append("bomb_flag_shift")
    return tags


def score_legal_moves(board: Board, player: Player, legal_moves: List[Tuple[Position, Position]], top_n: int = 30) -> List[Dict[str, Any]]:
    """对合法走法进行轻量评分与标签生成，并挑选约 top_n 条结果。
    仅依赖公开信息：若防守方不可见，则不使用其真实类型。
    返回结构适合直接发送给 LLM：包含 id/from/to/piece_id/risk_level/tactics/reason。
    """
    scored: List[ScoredMove] = []
    for idx, (from_pos, to_pos) in enumerate(legal_moves):
        from_cell = board.get_cell(from_pos)
        if not from_cell or not from_cell.piece:
            # 非法输入，跳过
            continue
        attacker = from_cell.piece
        score, attack_ev, risk, pos_gain, defense = evaluate_move(board, player, attacker, from_pos, to_pos)
        # 机动与信息增益用于战术标签（解释），不影响数值已在评估中体现
        mob = _mobility_potential(board, attacker, to_pos)
        info = _info_gain(board, to_pos)
        tactics = _tactics(board, attacker, from_pos, to_pos, attack_ev, pos_gain, defense)
        behavior = classify_move(board, player, from_pos, to_pos)
        reason = _build_reason(score, risk, attack_ev, tactics)
        scored.append(ScoredMove(
            idx=idx,
            from_pos=from_pos,
            to_pos=to_pos,
            piece_id=attacker.piece_id,
            score=float(round(score, 3)),
            risk_level=_label_risk(risk),
            reward_level="low",  # 占位，不对外暴露
            tactics=tactics,
            reason=reason,
        ))
    # 保留内部排序与多样性选择逻辑，但不向外暴露分数
    scored.sort(key=lambda m: m.score, reverse=True)
    # 多样性选择（简单版）：优先各战术类型的前若干条，再补齐
    selection: List[ScoredMove] = []
    quotas = {
        "attack_win": 8,
        "attack_trade": 3,
        "attack_risky": 1,
        "rail_sprint": 6,
        "central_control": 4,
        "central_probe": 4,
        "defend_flag": 6,
        "camp_hold": 3,  # 略微提升行营驻守占比
        "scout": 2,
        "bomb_probe": 4,
        "bomb_suspect_probe": 4,
        "engineer_flag_probe": 4,
        "escort": 5,
        "escort_fake": 4,
        "fake_carrier": 3,
        "bomb_cover": 5,
        "bomb_trap": 5,
        "bomb_feint": 3,
        "feint_refill": 4,
        "trap_rebuild": 4,
        "bomb_flag_shift": 4,
        "well_guarded": 3,
        "isolated_carrier": 2,
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
    # 输出为字典列表（不包含 score 与 reward_level）
    result: List[Dict[str, Any]] = []
    for i, m in enumerate(selection):
        item = {
            "id": i,
            "from": {"row": m.from_pos.row, "col": m.from_pos.col},
            "to": {"row": m.to_pos.row, "col": m.to_pos.col},
            "piece_id": m.piece_id,
            "risk_level": m.risk_level,
            "tactics": m.tactics,
            "reason": m.reason,
        }
        # 附加行为类别，便于上层做风格筛选（对LLM兼容且不破坏现有消费者）
        item["behavior"] = classify_move(board, player, m.from_pos, m.to_pos)
        result.append(item)
    return result


def _build_reason(score: float, risk: float, attack_ev: float, tactics: List[str]) -> str:
    parts: List[str] = []
    parts.append(f"风险={_label_risk(risk)}")
    if attack_ev > 0.15:
        parts.append("吃子存活")
    elif -0.1 <= attack_ev <= 0.15:
        parts.append("可能互换")
    elif attack_ev < -0.1:
        parts.append("进攻风险")
    if tactics:
        parts.append(",".join(tactics[:2]))
    return "；".join(parts)



# === 新增：通用数值评估与风格/反击集成 ===

def evaluate_move(board: Board, player: Player, attacker: Piece, from_pos: Position, to_pos: Position, history: Optional[HistoryRecorder] = None) -> Tuple[float, float, float, float, float]:
    """计算单步走法的数值评估。
    返回 (score, attack_ev, risk, pos_gain, def_gain)。
    """
    attack_ev = _attack_ev(board, attacker, to_pos, player, history)
    pos_gain = _positional_gain(board, attacker, to_pos)
    risk = _exposure_risk(board, attacker.player, to_pos)
    def_gain = _defense_value(board, player, to_pos)
    # 炸弹攻击：到达后棋子同归于尽，位置收益与暴露风险应弱化
    try:
        to_cell_for_risk = board.get_cell(to_pos)
        is_attack_here = bool(to_cell_for_risk and to_cell_for_risk.piece)
        if attacker.is_bomb() and is_attack_here:
            # 炸弹会在战斗后消失，实际没有“站位风险”和“控位收益”
            pos_gain *= 0.0
            risk *= 0.2
    except Exception:
        pass
    # 护航编队加成与孤立惩罚（内联实现）
    escort = 0.0
    # 找到本方司/军位置
    carriers: List[Position] = []
    for pos, cell in board.cells.items():
        if not cell or not cell.piece:
            continue
        p = cell.piece
        if p.player == player and p.piece_type in (PieceType.COMMANDER, PieceType.GENERAL):
            carriers.append(pos)
    if len(carriers) > 0:
        # 小子靠近司/军：加分
        if attacker.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON):
            if any(abs(to_pos.row - cp.row) + abs(to_pos.col - cp.col) == 1 for cp in carriers):
                escort += 0.25
        # 司/军自身的护航状态
        if attacker.piece_type in (PieceType.COMMANDER, PieceType.GENERAL):
            # 本方小子相邻数
            small_adj = 0
            for adj in board.get_adjacent_positions(to_pos):
                ac = board.get_cell(adj)
                if ac and ac.piece and ac.piece.player == player and ac.piece.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON):
                    small_adj += 1
            # 未被推断为大子时更保守
            not_revealed = not _is_revealed_high(attacker)
            # 敌方未知邻居（可能伪装炸弹）
            unknown_enemy_adj = 0
            for adj in board.get_adjacent_positions(to_pos):
                ac = board.get_cell(adj)
                if ac and ac.piece and (not board._are_allied(ac.piece.player, player)) and (not ac.piece.visible):
                    unknown_enemy_adj += 1
            if small_adj == 0 and (unknown_enemy_adj >= 1 or _is_center(to_pos)):
                escort -= 0.22 if not_revealed else 0.16
            else:
                escort += min(small_adj, 2) * (0.12 if not_revealed else 0.08)
    # 炸弹掩护阵地（炸弹跟随师/旅/军/司）：形成反击陷阱
    bomb_cover = 0.0
    allied_bombs: List[Position] = []
    for pos, cell in board.cells.items():
        if not cell or not cell.piece:
            continue
        p = cell.piece
        if p.player == player and p.is_bomb():
            allied_bombs.append(pos)
    # 炸弹移动到高子附近：增强反击能力
    if attacker.is_bomb():
        # 邻接本方师/旅/军/司
        near_high = False
        for adj in board.get_adjacent_positions(to_pos):
            ac = board.get_cell(adj)
            if ac and ac.piece and ac.piece.player == player and ac.piece.piece_type in (PieceType.DIVISION, PieceType.BRIGADE, PieceType.REGIMENT, PieceType.GENERAL, PieceType.COMMANDER):
                near_high = True
                # 若该高子处于“前线”（中心或邻近未知敌子），提高加成
                unknown_enemy_adj = 0
                for a2 in board.get_adjacent_positions(adj):
                    c2 = board.get_cell(a2)
                    if c2 and c2.piece and (not board._are_allied(c2.piece.player, player)) and (not c2.piece.visible):
                        unknown_enemy_adj += 1
                bomb_cover += (0.3 if (_is_center(adj) or unknown_enemy_adj >= 1) else 0.15)
        # 若未靠近高子但进入中心，适度加分（中心威慑）
        if not near_high and _is_center(to_pos):
            bomb_cover += 0.08
    # 高子移动到炸弹附近：构成“炸弹陷阱”
    if attacker.piece_type in (PieceType.DIVISION, PieceType.BRIGADE, PieceType.REGIMENT, PieceType.GENERAL, PieceType.COMMANDER):
        # 若目标位置相邻有本方炸弹
        adj_bomb_positions = [bp for bp in allied_bombs if abs(to_pos.row - bp.row) + abs(to_pos.col - bp.col) == 1]
        if adj_bomb_positions:
            # 前线位与未知邻居更高
            unknown_enemy_adj = 0
            for adj in board.get_adjacent_positions(to_pos):
                ac = board.get_cell(adj)
                if ac and ac.piece and (not board._are_allied(ac.piece.player, player)) and (not ac.piece.visible):
                    unknown_enemy_adj += 1
            base_trap = (0.28 if (_is_center(to_pos) or unknown_enemy_adj >= 1) else 0.15)
            # 若该炸弹此前没有邻接任何本方高子，则视为“陷阱补位”，额外加分
            trap_rebuild_bonus = 0.0
            for bp in adj_bomb_positions:
                if _count_adjacent_allied_high(board, player, bp) == 0:
                    trap_rebuild_bonus += 0.12
            # 前排位置（本地第1行）进一步加成
            if _is_first_row(board, player, to_pos):
                trap_rebuild_bonus += 0.08
            bomb_cover += base_trap + trap_rebuild_bonus
    # 假炸弹佯攻与补位：未暴露的非炸弹贴身高子
    if (not attacker.is_bomb()) and (not getattr(attacker, "kill_count", 0) > 0):
        if hasattr(attacker, "visible") and (not attacker.visible):
            # 找到相邻的目标高子（优先第一个）
            target_high: Optional[Position] = None
            for cp in carriers:
                if abs(to_pos.row - cp.row) + abs(to_pos.col - cp.col) == 1:
                    target_high = cp
                    break
            if target_high:
                feint_bonus = 0.1  # 基础佯攻值
                # 若该高子相邻已有本方炸弹，形成“真炸弹+假炸弹”的双层防御
                bomb_adj_cnt = _count_adjacent_allied_bombs(board, player, target_high)
                if bomb_adj_cnt > 0:
                    feint_bonus += 0.12
                # 前排（第1行）额外加成
                if _is_first_row(board, player, target_high):
                    feint_bonus += 0.08
                # 若该高子当前没有任何未暴露的非炸弹相邻，则视为“假炸弹补位”
                unrevealed_nonbomb_adj = _count_adjacent_allied_unrevealed_nonbomb(board, player, target_high)
                if unrevealed_nonbomb_adj == 0:
                    feint_bonus += 0.18
                bomb_cover += feint_bonus
    # 假司令编队加成与孤立惩罚（轻于真司/军）
    fake_escort = 0.0
    fake_carriers: List[Position] = []
    for pos, cell in board.cells.items():
        if not cell or not cell.piece:
            continue
        p = cell.piece
        if p.player == player and _is_fake_commander(p):
            fake_carriers.append(pos)
    if len(fake_carriers) > 0:
        if attacker.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON):
            if any(abs(to_pos.row - cp.row) + abs(to_pos.col - cp.col) == 1 for cp in fake_carriers):
                fake_escort += 0.18
        if _is_fake_commander(attacker):
            # 小子护航数量
            small_adj = 0
            for adj in board.get_adjacent_positions(to_pos):
                ac = board.get_cell(adj)
                if ac and ac.piece and ac.piece.player == player and ac.piece.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON):
                    small_adj += 1
            # 邻近未知敌子与中心孤立轻微扣分
            unknown_enemy_adj = 0
            for adj in board.get_adjacent_positions(to_pos):
                ac = board.get_cell(adj)
                if ac and ac.piece and (not board._are_allied(ac.piece.player, player)) and (not ac.piece.visible):
                    unknown_enemy_adj += 1
            if small_adj == 0 and (unknown_enemy_adj >= 1 or _is_center(to_pos)):
                fake_escort -= 0.1
            else:
                fake_escort += min(small_adj, 2) * 0.06
    # 权重组合（调整以鼓励以“司/军”为核心的战术，同时兼顾防守与稳健）
    w_attack, w_pos, w_risk, w_mob, w_info, w_def, w_escort, w_fake, w_bomb = 1.3, 0.24, 0.32, 0.18, 0.18, 0.6, 0.35, 0.25, 0.3
    # 若防御被击穿的近似条件：存在未知敌子靠近我方大本营；炸弹尚未形成高子陷阱
    try:
        if attacker.is_bomb():
            hqs = _friendly_hq_positions(board, player)
            if hqs and _unknown_enemies_near(board, player, hqs, radius=2):
                # 炸弹向军旗附近转移：靠近任一大本营的曼哈顿距离<=2加分
                nearest_hq_dist = min(abs(to_pos.row - hq.row) + abs(to_pos.col - hq.col) for hq in hqs)
                if nearest_hq_dist <= 2:
                    bomb_cover += 0.16
                # 若当前所有炸弹附近都没有高子，进一步加成
                no_traps = all(_count_adjacent_allied_high(board, player, bp) == 0 for bp in allied_bombs)
                if no_traps and nearest_hq_dist <= 2:
                    bomb_cover += 0.08
    except Exception:
        pass
    # 在旗处高威胁时提升防守权重（简单判定：旗邻有敌子）
    if def_gain > 0.0:
        w_def = 0.9
    # 机动与信息增益纳入基础评估
    mob = _mobility_potential(board, attacker, to_pos)
    info = _info_gain(board, to_pos)
    score = (w_attack * attack_ev +
             w_pos * pos_gain -
             w_risk * risk +
             w_mob * mob +
             w_info * info +
             w_def * def_gain +
             w_escort * escort +
             w_fake * fake_escort +
             w_bomb * bomb_cover)
    # 对于司令/军长的“非击杀暴露”进行轻微惩罚：未被对手推断为大子的情况下尽量避免冒头
    try:
        to_cell = board.get_cell(to_pos)
        is_attack = bool(to_cell and to_cell.piece)
        if not is_attack and attacker.piece_type in (PieceType.COMMANDER, PieceType.GENERAL) and (not _is_revealed_high(attacker)):
            if risk >= 0.25:
                score -= 0.12
        # 适度惩罚：中心附近小子非吃子走法且邻有未知敌子，避免“随意送子助敌成假司令”
        if not is_attack and attacker.piece_type in (PieceType.BATTALION, PieceType.COMPANY, PieceType.PLATOON) and _is_center(to_pos):
            unknown_enemy_adj = 0
            for adj in board.get_adjacent_positions(to_pos):
                ac = board.get_cell(adj)
                if ac and ac.piece and (not board._are_allied(ac.piece.player, player)) and (not ac.piece.visible):
                    unknown_enemy_adj += 1
            if unknown_enemy_adj >= 1:
                score -= 0.08
        # 后两排“威慑”移动限制：非吃子离开后两排给惩罚；若受敌方工兵威胁则豁免
        if _is_last_two_rows(board, attacker.player, from_pos) and not is_attack:
            # 检查是否存在敌方工兵2步内可达（近似威胁）
            engineer_threat = False
            for pos2, cell2 in board.cells.items():
                if not cell2 or not cell2.piece:
                    continue
                p2 = cell2.piece
                if board._are_allied(p2.player, player):
                    continue
                if p2.piece_type != PieceType.ENGINEER:
                    continue
                frontier = [pos2]
                visited = {(pos2.row, pos2.col)}
                for _ in range(2):
                    nxt = []
                    for cur in frontier:
                        for adj in board.get_adjacent_positions(cur):
                            key = (adj.row, adj.col)
                            if key in visited:
                                continue
                            visited.add(key)
                            nxt.append(adj)
                            if adj.row == from_pos.row and adj.col == from_pos.col:
                                engineer_threat = True
                                break
                        if engineer_threat:
                            break
                    frontier = nxt
                    if engineer_threat:
                        break
                if engineer_threat:
                    break
            if not engineer_threat:
                # 依据子力大小分级惩罚（不应过重）
                if attacker.piece_type in (PieceType.COMMANDER, PieceType.GENERAL):
                    score -= 0.24
                elif attacker.piece_type == PieceType.DIVISION:
                    score -= 0.20
                elif attacker.piece_type in (PieceType.BRIGADE, PieceType.REGIMENT):
                    score -= 0.16
                else:
                    score -= 0.12
        # 工兵保守策略：避免非目的性中心/铁路行动；保留至少两名工兵
        if attacker.piece_type == PieceType.ENGINEER:
            eng_cnt = _alive_engineer_count(board, player)
            if not is_attack:
                if _is_center(to_pos):
                    score -= 0.12
                cell = board.get_cell(to_pos)
                if cell and cell.cell_type == CellType.RAILWAY:
                    near_flag = _is_near_enemy_flag(board, player, to_pos)
                    adj_visible_div = False
                    for adj in board.get_adjacent_positions(to_pos):
                        c2 = board.get_cell(adj)
                        if c2 and c2.piece and (not board._are_allied(c2.piece.player, player)) and c2.piece.visible and c2.piece.piece_type == PieceType.DIVISION:
                            adj_visible_div = True
                            break
                    if not near_flag and not adj_visible_div:
                        score -= 0.06
                    # 新增：无意义拐弯惩罚——仅工兵能拐弯，此举会暴露身份
                    try:
                        from_cell = board.get_cell(from_pos)
                        to_cell = board.get_cell(to_pos)
                        if from_cell and to_cell and from_cell.cell_type == CellType.RAILWAY and to_cell.cell_type == CellType.RAILWAY:
                            straight_positions = board.get_railway_straight_reachable_positions(from_pos)
                            connected_positions = board.get_railway_connected_positions(from_pos)
                            is_turning = (to_pos in connected_positions) and (to_pos not in straight_positions)
                            if is_turning:
                                # 非攻击且没有实际收益（机动/信息/逼近敌旗），视为“毫无意义挪动”
                                mob_from = _mobility_potential(board, attacker, from_pos)
                                mob_to = _mobility_potential(board, attacker, to_pos)
                                info_gain = _info_gain(board, to_pos)
                                mob_no_improve = mob_to <= (mob_from + 0.02)
                                if mob_no_improve and (info_gain < 0.05) and (not near_flag):
                                    base_penalty = 0.12
                                    # 剩余工兵越少，越应避免暴露
                                    if eng_cnt <= 2:
                                        base_penalty += 0.06
                                    # 拐入中心区域再加一点惩罚
                                    if _is_center(to_pos):
                                        base_penalty += 0.02
                                    score -= base_penalty
                    except Exception:
                        pass
                # 当仅剩两名或更少工兵时，额外保守
                if eng_cnt <= 2:
                    score -= 0.06
    except Exception:
        pass
    return float(round(score, 3)), attack_ev, risk, pos_gain, def_gain


def choose_best_move_styled(board: Board, player: Player, legal_moves: List[Tuple[Position, Position]], history: HistoryRecorder) -> Optional[Dict[str, Any]]:
    """带风格与“反击”优先的走法选择：
    1) 若上一轮（自我方上一次行动到此次行动之间）存在“敌方吃掉我方”的记录，尝试直接反击该敌子；
       - 可反击且存在候选时，在该候选池中进行深度搜索选择最佳；
    2) 否则按玩家风格概率采样行为类别，在该类别候选池（若为空则使用全体候选）中进行深度搜索选择最佳。
    若深度搜索无法产生走法，返回 None（由上层决定是否跳过本回合）。
    """
    from server.strategies.search import search_best_move_in_pool, SearchConfig
    if not isinstance(legal_moves, list) or len(legal_moves) == 0:
        return None

    # 识别可反击目标
    targets = _find_counterattack_targets(history, player)
    counter_moves: List[Tuple[int, Position, Position]] = []
    if targets:
        target_ids: Set[str] = set(targets)
        for idx, (from_pos, to_pos) in enumerate(legal_moves):
            to_cell = board.get_cell(to_pos)
            if to_cell and to_cell.piece and to_cell.piece.piece_id in target_ids:
                counter_moves.append((idx, from_pos, to_pos))
    if counter_moves:
        # 使用深度搜索在反击池中选择最佳
        candidates = [(fp, tp) for (_, fp, tp) in counter_moves]
        cfg = SearchConfig(depth=3, beam_width=8, discount=0.95, time_limit_ms=5000)
        sr = search_best_move_in_pool(board, player, candidates, preferred_category=None, config=cfg, history=history)
        if sr.best_move:
            fp, tp = sr.best_move
            # 找出返回的走法对应的原始 id
            try:
                idx = legal_moves.index((fp, tp))
            except ValueError:
                # 回退：在 counter_moves 中匹配
                idx = next((i for (i, fpp, tpp) in counter_moves if fpp == fp and tpp == tp), 0)
            attacker = board.get_cell(fp).piece if board.get_cell(fp) else None
            pid = attacker.piece_id if attacker else None
            return {
                "id": int(idx),
                "from": {"row": fp.row, "col": fp.col},
                "to": {"row": tp.row, "col": tp.col},
                "piece_id": pid,
            }

    # 无反击或不可反击：按玩家风格采样类别
    category = _sample_behavior_category(player)
    filtered: List[Tuple[int, Position, Position]] = []
    for idx, (from_pos, to_pos) in enumerate(legal_moves):
        if classify_move(board, player, from_pos, to_pos) == category:
            filtered.append((idx, from_pos, to_pos))
    # 若该类别为空，回退到全体候选
    pool = filtered if filtered else [(i, fp, tp) for i, (fp, tp) in enumerate(legal_moves)]
    # 用深度搜索在风格池（或全体）中选择最佳
    candidates = [(fp, tp) for (_, fp, tp) in pool]
    cfg = SearchConfig(depth=3, beam_width=8, discount=0.95, time_limit_ms=5000)
    sr = search_best_move_in_pool(board, player, candidates, preferred_category=category if filtered else None, config=cfg, history=history)
    if sr.best_move:
        fp, tp = sr.best_move
        try:
            idx = legal_moves.index((fp, tp))
        except ValueError:
            idx = next((i for (i, fpp, tpp) in pool if fpp == fp and tpp == tp), 0)
        attacker = board.get_cell(fp).piece if board.get_cell(fp) else None
        pid = attacker.piece_id if attacker else None
        return {
            "id": int(idx),
            "from": {"row": fp.row, "col": fp.col},
            "to": {"row": tp.row, "col": tp.col},
            "piece_id": pid,
        }
    # 深度搜索未产生结果：返回 None，由上层处理（例如跳过回合）
    return None


# === 辅助：反击目标与风格采样 ===

FACTION_PREFIX = {Player.PLAYER1: "south", Player.PLAYER2: "west", Player.PLAYER3: "north", Player.PLAYER4: "east"}

def _find_counterattack_targets(history: HistoryRecorder, player: Player) -> List[str]:
    """在上一轮（自我方上一手到当前手之间）中，被敌方吃掉的我方棋子的攻击者ID集合。
    条件：outcome == 'attack_attacker_wins'；排除互毁与我方防守胜利。
    返回攻击者 piece_id 列表（长度为 0-2）。
    """
    if not history or not getattr(history, "records", None):
        return []
    my_faction = FACTION_PREFIX.get(player, "south")
    attackers: List[str] = []
    # 从末尾向前扫描，直到遇到我方的上一手为止
    for rec in reversed(history.records):
        pf = getattr(rec, "player_faction", None)
        if pf == my_faction:
            # 到达我方上一手，停止
            break
        outcome = getattr(rec, "outcome", "move")
        if outcome == "attack_attacker_wins":
            # 若死亡列表包含我方棋子ID，则该攻击者纳入反击目标
            dead_ids = getattr(rec, "dead_piece_ids", None) or []
            if any((isinstance(d, str) and d.startswith(my_faction + "_")) for d in dead_ids):
                atk_id = getattr(rec, "piece_id", None)
                if isinstance(atk_id, str):
                    attackers.append(atk_id)
        # 最多保留2个目标
        if len(attackers) >= 2:
            break
    return attackers


def _sample_behavior_category(player: Player) -> str:
    """按玩家风格概率采样行为类别。
    - player1：偏防守 40%，进攻30%，试探30%
    - player2：偏试探 40%，进攻35%，防守25%
    - player3：偏进攻 60%，试探30%，防守10%
    - 其他玩家（player4）：均衡 1/3。
    """
    if player == Player.PLAYER1:
        probs = {CATEGORY_DEFEND: 0.40, CATEGORY_ATTACK: 0.30, CATEGORY_PROBE: 0.30}
    elif player == Player.PLAYER2:
        probs = {CATEGORY_PROBE: 0.40, CATEGORY_ATTACK: 0.35, CATEGORY_DEFEND: 0.25}
    elif player == Player.PLAYER3:
        probs = {CATEGORY_ATTACK: 0.60, CATEGORY_PROBE: 0.30, CATEGORY_DEFEND: 0.10}
    else:
        probs = {CATEGORY_ATTACK: 1/3, CATEGORY_PROBE: 1/3, CATEGORY_DEFEND: 1/3}
    # 归一化，防止输入概率和不为1
    total = sum(probs.values()) or 1.0
    norm = {k: v / total for k, v in probs.items()}
    # 累积分布采样
    r = random.random()
    cumulative = 0.0
    for k in (CATEGORY_ATTACK, CATEGORY_PROBE, CATEGORY_DEFEND):
        cumulative += norm.get(k, 0.0)
        if r <= cumulative:
            return k
    # 兜底
    return CATEGORY_DEFEND