#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
军棋棋子定义
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional

class PieceType(Enum):
    """棋子类型枚举"""
    COMMANDER = "司令"      # 司令 - 最大
    GENERAL = "军长"       # 军长
    DIVISION = "师长"      # 师长
    BRIGADE = "旅长"       # 旅长
    REGIMENT = "团长"      # 团长
    BATTALION = "营长"     # 营长
    COMPANY = "连长"       # 连长
    PLATOON = "排长"       # 排长
    ENGINEER = "工兵"      # 工兵 - 最小，但可以挖地雷
    BOMB = "炸弹"          # 炸弹 - 特殊单位
    MINE = "地雷"          # 地雷 - 特殊单位
    FLAG = "军旗"          # 军旗 - 特殊单位

class Player(Enum):
    """玩家枚举"""
    PLAYER1 = 1  # 下方玩家（人类玩家）
    PLAYER2 = 2  # 左方玩家
    PLAYER3 = 3  # 上方玩家（对家）
    PLAYER4 = 4  # 右方玩家

@dataclass
class Piece:
    """棋子类"""
    piece_type: PieceType
    player: Player
    visible: bool = False  # 是否对其他玩家可见
    kill_count: int = 0    # 战绩：累计击杀数
    # 为LLM交互添加：唯一棋子ID（如 south_001 / north_023），在开始游戏时统一分配
    piece_id: Optional[str] = None
    
    def __str__(self):
        return f"{self.player.name}的{self.piece_type.value}"
    
    def get_power(self) -> int:
        """获取棋子战斗力，数值越大越强"""
        power_map = {
            PieceType.COMMANDER: 10,
            PieceType.GENERAL: 9,
            PieceType.DIVISION: 8,
            PieceType.BRIGADE: 7,
            PieceType.REGIMENT: 6,
            PieceType.BATTALION: 5,
            PieceType.COMPANY: 4,
            PieceType.PLATOON: 3,
            PieceType.ENGINEER: 2,
            PieceType.BOMB: 1,      # 炸弹特殊处理
            PieceType.MINE: 0,      # 地雷特殊处理
            PieceType.FLAG: -1      # 军旗最弱
        }
        return power_map[self.piece_type]
    
    def can_move(self) -> bool:
        """判断棋子是否可以移动"""
        # 地雷和军旗不能移动
        return self.piece_type not in [PieceType.MINE, PieceType.FLAG]
    
    def is_engineer(self) -> bool:
        """判断是否为工兵"""
        return self.piece_type == PieceType.ENGINEER
    
    def is_bomb(self) -> bool:
        """判断是否为炸弹"""
        return self.piece_type == PieceType.BOMB
    
    def is_mine(self) -> bool:
        """判断是否为地雷"""
        return self.piece_type == PieceType.MINE
    
    def is_flag(self) -> bool:
        """判断是否为军旗"""
        return self.piece_type == PieceType.FLAG

# 每个玩家的初始棋子配置
INITIAL_PIECES = {
    PieceType.COMMANDER: 1,
    PieceType.GENERAL: 1,
    PieceType.DIVISION: 2,
    PieceType.BRIGADE: 2,
    PieceType.REGIMENT: 2,
    PieceType.BATTALION: 2,
    PieceType.COMPANY: 3,
    PieceType.PLATOON: 3,
    PieceType.ENGINEER: 3,
    PieceType.BOMB: 2,
    PieceType.MINE: 3,
    PieceType.FLAG: 1
}

def create_player_pieces(player: Player) -> list[Piece]:
    """为指定玩家创建初始棋子"""
    pieces = []
    for piece_type, count in INITIAL_PIECES.items():
        for _ in range(count):
            pieces.append(Piece(piece_type, player))
    return pieces