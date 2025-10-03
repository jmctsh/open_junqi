#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
棋局历史记录模块：用于记录每一步走子与战斗结果，面向LLM的提示与回放。
坐标统一使用“移动方阵营的本地坐标”(row, col)，必要时系统后台自行转换为全局坐标。
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
import json

@dataclass
class MoveRecord:
    """单步走子与战斗结果记录"""
    turn: int                      # 回合序号（从1开始）
    player_faction: str            # 走子方阵营（south/west/north/east）
    piece_id: str                  # 移动的棋子ID（如 south_017）
    from_local: tuple[int, int]    # 起点（移动方本地坐标）
    to_local: tuple[int, int]      # 终点（移动方本地坐标）
    outcome: str                   # "move" | "attack_attacker_wins" | "attack_defender_wins" | "attack_both_die"
    defender_piece_id: Optional[str] = None   # 防守方棋子ID（若有战斗）
    dead_piece_ids: Optional[List[str]] = None  # 死亡的棋子ID列表（可能为空）

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # 统一None为空列表，便于下游消费
        if d.get('dead_piece_ids') is None:
            d['dead_piece_ids'] = []
        return d

class HistoryRecorder:
    """棋局历史记录器"""
    def __init__(self) -> None:
        self.records: List[MoveRecord] = []

    def add_record(self, record: MoveRecord) -> None:
        self.records.append(record)

    def clear(self) -> None:
        self.records.clear()

    def to_list(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.records]

    def to_json(self, ensure_ascii: bool = False) -> str:
        return json.dumps(self.to_list(), ensure_ascii=ensure_ascii)