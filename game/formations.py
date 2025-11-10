#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
军棋名阵模板
- 模板统一以南方玩家的本地坐标（6行x5列）书写：第1行为南方最上排，第6行为最下排；列1在最左。
- 其他阵营在布阵时会自动旋转映射到其区域。
- 网格字符到棋子类型的映射：
  司→司令，军→军长，师→师长，旅→旅长，团→团长，营→营长，连→连长，排→排长，兵→工兵，炸/弹→炸弹，雷→地雷，旗→军旗。
  空白或"·"表示不放棋子（通常对应行营）。
"""

from typing import Dict, List
from .piece import PieceType

# 字符到 PieceType 的映射
CHAR_TO_PIECE: Dict[str, PieceType] = {
    '司': PieceType.COMMANDER,
    '令': PieceType.COMMANDER,
    '军': PieceType.GENERAL,
    '师': PieceType.DIVISION,
    '旅': PieceType.BRIGADE,
    '团': PieceType.REGIMENT,
    '营': PieceType.BATTALION,
    '连': PieceType.COMPANY,
    '排': PieceType.PLATOON,
    '兵': PieceType.ENGINEER,
    '炸': PieceType.BOMB,
    '弹': PieceType.BOMB,
    '雷': PieceType.MINE,
    '旗': PieceType.FLAG,
}

def normalize_row(row: str) -> str:
    """规范行内容为5列：去除空白，统一省略号，保留"·"表示空"""
    r = row.strip().replace(' ', '')
    # 将ASCII省略号"..."或".."规范为单个Unicode省略号“…”（占一列）
    r = r.replace('...', '…')
    r = r.replace('..', '…')
    return r

# 目前接收的5个名阵名称，网格暂留空；收到文本矩阵后可直接填入
# 每个模板为6行字符串（长度可为<=5），位置按本地坐标行列放置
FORMATIONS: Dict[str, List[str]] = {
    # 十大名阵（南方本地坐标 6×5）
    '河东狮吼': [
        '师兵连排师',
        '旅…连…炸',
        '炸营…团旅',
        '军…兵…连',
        '司兵营雷排',
        '团排雷旗雷',
    ],
    '午夜风铃': [
        '连旅司兵团',
        '师…炸…军',
        '团排…连兵',
        '排…营…营',
        '雷雷连师炸',
        '雷旗旅排兵',
    ],
    '飞花逐月': [
        '连司军兵师',
        '师…连…旅',
        '团弹…弹团',
        '营…排…营',
        '旅兵兵排雷',
        '雷旗雷排连',
    ],
    '飘香一剑': [
        '师兵连旅师',
        '团…连…炸',
        '团营…炸营',
        '司…兵…连',
        '军兵旅雷排',
        '排排雷旗雷',
    ],
    '于无声处': [
        '师兵军排营',
        '团…兵…旅',
        '师弹…连司',
        '弹…排…连',
        '营雷连雷团',
        '旅旗雷排兵',
    ],
    '乌龙摆尾': [
        '营兵团排连',
        '师…兵…司',
        '旅连…军排',
        '连…兵…团',
        '弹旅师营雷',
        '弹排雷旗雷',
    ],
    '三节阵': [
        '团排军兵令',
        '连…兵…团',
        '师弹…兵连',
        '排…连…旅',
        '雷营师弹旅',
        '雷旗雷排营',
    ],
    '狼来了': [
        '团兵旅师连',
        '营…兵…军',
        '司兵…弹师',
        '排…弹…团',
        '营旅连雷连',
        '排排雷旗雷',
    ],
    '雾山重剑': [
        '连排兵兵团',
        '师…连…司',
        '团营…营师',
        '炸…兵…旅',
        '军连旅雷炸',
        '雷旗雷排排',
    ],
}

def has_formation(name: str) -> bool:
    return name in FORMATIONS and len(FORMATIONS[name]) == 6

def list_formations() -> List[str]:
    return [n for n, g in FORMATIONS.items() if len(g) == 6]

def register_formation(name: str, grid: List[str]) -> bool:
    """注册或更新名阵；grid为6行，每行最多5字符（其余为空）"""
    if len(grid) != 6:
        return False
    FORMATIONS[name] = [normalize_row(r) for r in grid]
    return True

def char_to_piece_type(ch: str) -> PieceType | None:
    return CHAR_TO_PIECE.get(ch)