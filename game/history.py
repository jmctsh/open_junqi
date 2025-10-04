#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
棋局历史记录模块：用于记录每一步走子与战斗结果，面向LLM的提示与回放。
坐标统一使用“移动方阵营的本地坐标”(row, col)，必要时系统后台自行转换为全局坐标。
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Any
import json
import datetime

@dataclass
class MoveRecord:
    """单步走子与战斗结果记录（紧凑导出）"""
    turn: int                      # 回合序号（从1开始）
    player_faction: str            # 走子方阵营（south/west/north/east）
    piece_id: str                  # 移动的棋子ID（如 south_017）
    from_local: tuple[int, int]    # 起点（移动方本地坐标）
    to_local: tuple[int, int]      # 终点（移动方本地坐标）
    outcome: str                   # "move" | "attack_attacker_wins" | "attack_defender_wins" | "attack_both_die"
    defender_piece_id: Optional[str] = None   # 防守方棋子ID（若有战斗）
    dead_piece_ids: Optional[List[str]] = None  # 死亡的棋子ID列表（可能为空）
    # 新增：时间戳（ISO，精确到秒），默认在创建记录时生成
    ts: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec='seconds'))

    def to_dict(self) -> Dict[str, Any]:
        # 紧凑导出：仅保留时间戳、阵营、棋子ID、目标位置；若发生战斗则附加 outcome
        d: Dict[str, Any] = {
            "ts": self.ts,
            "player_faction": self.player_faction,
            "piece_id": self.piece_id,
            "to": self.to_local,
        }
        if self.outcome and self.outcome != "move":
            d["outcome"] = self.outcome
        return d

@dataclass
class ChatRecord:
    """单条聊天广播记录（紧凑导出）"""
    turn: int                      # 回合序号（从1开始）
    speaker_faction: str           # 发言方阵营（south/west/north/east）
    text: str                      # 广播文本内容
    target: str = "all"            # 目标（对全场或定向某阵营的称呼），默认 all

    def to_dict(self) -> Dict[str, Any]:
        # 紧凑导出：保留回合、发言方、文本与目标
        return {
            "turn": self.turn,
            "speaker_faction": self.speaker_faction,
            "text": self.text,
            "target": self.target,
        }

class HistoryRecorder:
    """棋局历史记录器"""
    def __init__(self) -> None:
        self.records: List[MoveRecord] = []
        # 新增：聊天广播历史（按时间顺序）
        self.chat_records: List[ChatRecord] = []

    def add_record(self, record: MoveRecord) -> None:
        self.records.append(record)

    def add_chat(self, record: ChatRecord) -> None:
        """添加一条聊天广播记录。"""
        self.chat_records.append(record)

    def clear(self) -> None:
        self.records.clear()

    def clear_chats(self) -> None:
        """清空聊天广播历史。"""
        self.chat_records.clear()

    def to_list(self) -> List[Dict[str, Any]]:
        # 导出紧凑的走子历史
        return [r.to_dict() for r in self.records]

    def to_chat_list(self) -> List[Dict[str, Any]]:
        """导出紧凑的聊天广播历史（字段：turn/speaker_faction/text/target）。"""
        return [r.to_dict() for r in self.chat_records]

    def to_json(self, ensure_ascii: bool = False) -> str:
        return json.dumps(self.to_list(), ensure_ascii=ensure_ascii)

    def to_chat_json(self, ensure_ascii: bool = False) -> str:
        """导出紧凁的聊天广播历史为JSON字符串。"""
        return json.dumps(self.to_chat_list(), ensure_ascii=ensure_ascii)