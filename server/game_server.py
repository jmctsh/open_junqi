#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通信模式：向 LLM 单向发送 + 历史注入
- 本模块仅开放 WebSocket 接口，接收 API 指令并执行行动，不向客户端返回数据或进行广播
- 当前仅支持棋子移动（type="move_piece"），其他指令暂不支持
"""

import json
from typing import Dict, Set, Optional, Any, List
from websockets.server import WebSocketServerProtocol
import logging
import asyncio
from dotenv import load_dotenv, find_dotenv

from game.game_logic import GameLogic
from game.piece import Player
from game.board import Position
from server.game_process import GameProcess
import threading
import websockets

# 加载 .env（若存在）
load_dotenv(find_dotenv(), override=False)

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GameServer:
    """简化版游戏服务器，仅提供WebSocket接口，移除AI与聊天相关逻辑"""

    def __init__(self, host: str = "localhost", port: int = 8765):
        self.host = host
        self.port = port
        self.clients: Dict[WebSocketServerProtocol, Player] = {}
        self.connected_players: Set[Player] = set()
        self.protocol_version: str = "1.0.0"
        self.game_logic = GameLogic()
        # 单机模式：固定南方位为本地玩家（保留，不在此模块处理真人连接）
        self.human_seat: Optional[Player] = None
        # 顶层分配的AI席位（非南方位），在游戏开始时由顶层调用进行配置
        self._pending_ai_seats: List[Player] = []
        self._ai_seats_configured: Set[Player] = set()
        # 进程层引用：用于注册规则层信号，实现回合切换驱动AI调度
        self.process: Optional[GameProcess] = None
        # WebSocket服务事件循环与线程（后台运行，避免阻塞Qt主循环）
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_thread: Optional[threading.Thread] = None

    def _seat_label(self, player: Player) -> str:
        mapping = {
            Player.PLAYER1: "player_south",
            Player.PLAYER2: "player_west",
            Player.PLAYER3: "player_north",
            Player.PLAYER4: "player_east",
        }
        return mapping.get(player, "unknown")


    # ============ 连接管理 ============
    async def register_client(self, websocket: WebSocketServerProtocol) -> Optional[Player]:
        """注册AI客户端连接：绑定为顶层在开局时已固定的席位（非南方位），不进行随机或占用检测。
        
        顶层在“点击游戏开始”时，已将 AI 的固定席位列表（通常为 PLAYER2/PLAYER3/PLAYER4）
        配置到 self._pending_ai_seats。每次AI客户端连接时，按该列表的既定顺序依次弹出并绑定。
        
        不处理真人玩家连接：真人通过顶层GUI鼠标操作，不走WebSocket。
        """
        # 必须已由顶层配置AI固定席位队列
        if not self._pending_ai_seats:
            logger.warning("AI席位队列为空：请在开局时调用 configure_ai_seats 配置固定席位")
            return None
        # 按固定顺序绑定席位（不做随机/占用检测）
        assigned: Player = self._pending_ai_seats.pop(0)
        self.clients[websocket] = assigned
        self.connected_players.add(assigned)
        logger.info(f"AI客户端已连接并绑定固定席位（{self._seat_label(assigned)}）")
        return assigned

    async def unregister_client(self, websocket: WebSocketServerProtocol):
        if websocket in self.clients:
            player = self.clients[websocket]
            del self.clients[websocket]
            self.connected_players.discard(player)
            logger.info(f"客户端断开连接（{self._seat_label(player)}）")

    def configure_ai_seats(self, seats: List[Player]) -> None:
        """由顶层在开局时调用，配置AI席位（不含南方真人）。复用顶层的席位管理，不在此重复实现。"""
        self._pending_ai_seats = [s for s in seats if s != Player.PLAYER1]
        self._ai_seats_configured = set(self._pending_ai_seats)
        logger.info(f"已配置AI席位: {[self._seat_label(s) for s in self._pending_ai_seats]}")

    async def unregister_ai_by_seat(self, seat: Player) -> None:
        """由顶层在AI淘汰或离席时调用：注销该席位的AI WebSocket客户端。"""
        ws_to_close = None
        for ws, p in list(self.clients.items()):
            if p == seat:
                ws_to_close = ws
                break
        if ws_to_close is not None:
            try:
                await ws_to_close.close()
            except Exception:
                pass
            await self.unregister_client(ws_to_close)
        # 从已配置列表中移除该席位，避免后续误分配
        self._pending_ai_seats = [s for s in self._pending_ai_seats if s != seat]
        self._ai_seats_configured.discard(seat)
        logger.info(f"AI离席并注销客户端（{self._seat_label(seat)}）")

    async def unregister_all_ai(self) -> None:
        """由顶层在游戏结束时调用：注销所有AI WebSocket客户端。"""
        for ws, p in list(self.clients.items()):
            try:
                await ws.close()
            except Exception:
                pass
            await self.unregister_client(ws)
        self._pending_ai_seats = []
        self._ai_seats_configured.clear()
        logger.info("所有AI玩家已离席并注销客户端")

    # ============ 公共状态 ============
    def get_public_game_state(self) -> Dict[str, Any]:
        gl = self.game_logic
        # 棋盘公开视图（包含地形类型与棋子ID/可动性）；并生成基于棋子所属阵营的本地坐标映射
        try:
            board_cells = []
            piece_id_local_map = []
            for position, cell in gl.board.cells.items():
                cell_type = getattr(cell.cell_type, "name", "NORMAL") if cell else "NORMAL"
                piece_info = None
                if cell and cell.piece:
                    pid = cell.piece.piece_id
                    piece_info = {
                        "player_id": cell.piece.player.value,
                        "piece_id": pid,
                        "visible": bool(getattr(cell.piece, "visible", False)),
                        "can_move": bool(cell.piece.can_move()),
                    }
                    # 将棋子位置转换为其所属阵营的本地坐标（用于LLM理解）
                    if pid:
                        lr, lc = gl._get_local_coords(position, cell.piece.player)
                        piece_id_local_map.append({
                            "piece_id": pid,
                            "player_id": cell.piece.player.value,
                            "local_row": lr,
                            "local_col": lc,
                        })
                board_cells.append({
                    "row": position.row,
                    "col": position.col,
                    "cell_type": cell_type,
                    "piece": piece_info,
                })
        except Exception:
            board_cells = []
            piece_id_local_map = []
        board_state: Dict[str, Any] = {
            "rows": gl.board.rows,
            "cols": gl.board.cols,
            # 每个格子的地形类型与其上的棋子（若有）
            "cells": board_cells,
            # 公开的“棋子ID-本地坐标-所属玩家”映射（ID含方位前缀，如 north_001）
            "pieces_by_id_local": piece_id_local_map,
        }
        setup_map: Dict[int, bool] = {p.value: bool(gl.setup_complete.get(p, False)) for p in Player}
        teams = {
            "south_north": [Player.PLAYER1.value, Player.PLAYER3.value],
            "east_west": [Player.PLAYER2.value, Player.PLAYER4.value],
        }
        seat_label_map = {
            Player.PLAYER1.value: "player_south",
            Player.PLAYER2.value: "player_west",
            Player.PLAYER3.value: "player_north",
            Player.PLAYER4.value: "player_east",
        }
        return {
            "state": gl.game_state.value,
            "current_player": gl.current_player.value,
            "current_seat_label": seat_label_map.get(gl.current_player.value),
            "teams": teams,
            "setup_complete": setup_map,
            "eliminated_players": [p.value for p in gl.eliminated_players],
            "board": board_state,
            "history": gl.history.to_list(),
            "seat_labels": seat_label_map,
        }

    def get_player_board_state(self, player: Player) -> Dict[str, Any]:
        """生成指定方位玩家的“玩家棋盘状态”：仅揭示该玩家自己棋子的真实棋面与位置，同时提供友军/敌军信息。"""
        gl = self.game_logic
        # 同轴与敌友关系
        axis_players = {Player.PLAYER1, Player.PLAYER3} if player in {Player.PLAYER1, Player.PLAYER3} else {Player.PLAYER2, Player.PLAYER4}
        allies = [p.value for p in axis_players if p != player]
        enemies = [p.value for p in {Player.PLAYER1, Player.PLAYER2, Player.PLAYER3, Player.PLAYER4} if p not in axis_players]
        # 自己可操控的棋子列表（棋面-ID-位置三元组，位置采用本地坐标）
        own_pieces = []
        own_piece_ids = []
        for position, cell in gl.board.cells.items():
            if cell.piece and cell.piece.player == player:
                pid = cell.piece.piece_id
                lr, lc = gl._get_local_coords(position, player)
                own_pieces.append({
                    "piece_id": pid,
                    "piece_type": cell.piece.piece_type.value,
                    "local_row": lr,
                    "local_col": lc,
                    "controllable": True,
                })
                if pid:
                    own_piece_ids.append(pid)
        return {
            "for_player_id": player.value,
            "for_seat_label": self._seat_label(player),
            "allies": allies,
            "enemies": enemies,
            "own_piece_ids": own_piece_ids,
            "own_pieces": own_pieces,
            # 附带公开棋盘视图，AI可结合使用以判断敌我分布（ID前缀携带方位信息）
            "public": self.get_public_game_state(),
        }

    # ============ 消息处理 ============
    async def handle_message(self, websocket: WebSocketServerProtocol, message: str):
        return await self._handle_message_impl(websocket, message)

    async def _handle_message_impl(self, websocket: WebSocketServerProtocol, message: str):
        try:
            data = json.loads(message)
            message_type = data.get("type")
            player = self.clients.get(websocket)
            logger.info(f"[DEBUG] 收到消息: type={message_type}, from={getattr(player, 'name', 'UNKNOWN')}")
            if not player:
                return
            if message_type == "move_piece":
                await self.handle_move_piece(websocket, data, player)
            else:
                logger.info(f"忽略不支持的消息类型: {message_type}")
        except json.JSONDecodeError:
            logger.warning("无效的JSON格式")
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            return

    # ============ 具体操作 ============
    def set_game_logic(self, gl: GameLogic) -> None:
        """注入顶层的 GameLogic 实例，使通过API执行的行动直接作用于同一个规则层。"""
        self.game_logic = gl
        # 若已绑定进程层，则注册四个信号回调，确保规则层的回合切换通知到进程层
        if self.process is not None:
            try:
                # 将规则层引用注入进程层，供其构建 public_state 与合法走法
                self.process.set_game_logic(gl)
            except Exception:
                pass
            try:
                self.game_logic.set_signal_handlers(
                    on_game_started=self.process.on_game_started,
                    on_turn_changed=self.process.on_turn_changed,
                    on_player_eliminated=self._on_player_eliminated_bridge,
                    on_game_finished=self._on_game_finished_bridge,
                )
            except Exception:
                # 保守处理：不阻断初始化
                pass
    # 新增：注册进程层，并与规则层的信号接线
    def set_game_process(self, proc: GameProcess) -> None:
        """注入进程管理器 GameProcess，并与规则层的四个信号完成接线。"""
        self.process = proc
        if getattr(self, "game_logic", None) is not None:
            try:
                # 注入规则层引用，便于构建 public_state
                proc.set_game_logic(self.game_logic)
            except Exception:
                pass
            try:
                self.game_logic.set_signal_handlers(
                    on_game_started=proc.on_game_started,
                    on_turn_changed=proc.on_turn_changed,
                    on_player_eliminated=self._on_player_eliminated_bridge,
                    on_game_finished=self._on_game_finished_bridge,
                )
            except Exception:
                # 保守处理：不阻断接线
                pass
    async def handle_move_piece(self, websocket: WebSocketServerProtocol, data: Dict[str, Any], player: Player):
        try:
            src = data.get("from") or {}
            dst = data.get("to") or {}
            src_pos = Position(row=int(src.get("row")), col=int(src.get("col")))
            dst_pos = Position(row=int(dst.get("row")), col=int(dst.get("col")))
        except Exception:
            return
        if not self.game_logic.move_piece(src_pos, dst_pos):
            return
        # 不向客户端返回数据或进行广播；历史记录由 game_logic 维护
        return
            
    def apply_move_piece(self, src_pos: Position, dst_pos: Position) -> bool:
        """同步执行走子动作（供进程层/AI消费回调直接调用），返回是否成功。"""
        if not getattr(self, "game_logic", None):
            return False
        return bool(self.game_logic.move_piece(src_pos, dst_pos))

    def apply_move_command(self, data: Dict[str, Any]) -> bool:
        """解析来自AI的通用动作字典并执行（兼容 {'from':..., 'to':...} 与 {'move': {'from':..., 'to':...}} 两种结构）。"""
        try:
            move = data.get("move") if isinstance(data, dict) else None
            if isinstance(move, dict):
                src = move.get("from") or {}
                dst = move.get("to") or {}
            else:
                src = data.get("from") or {}
                dst = data.get("to") or {}
            src_pos = Position(row=int(src.get("row")), col=int(src.get("col")))
            dst_pos = Position(row=int(dst.get("row")), col=int(dst.get("col")))
        except Exception:
            return False
        return self.apply_move_piece(src_pos, dst_pos)
            
    # ============ 信号桥接：淘汰与结束 ============
    # ============ WebSocket 服务启动/停止 ============
    def start_ws_server(self):
        """在后台线程启动WebSocket服务器并保持运行。"""
        if self._ws_thread and self._ws_thread.is_alive():
            return
        def runner():
            try:
                loop = asyncio.new_event_loop()
                self._ws_loop = loop
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._serve_forever())
            except Exception as e:
                try:
                    logger.exception(f"WS服务器运行异常: {e}")
                except Exception:
                    pass
        self._ws_thread = threading.Thread(target=runner, daemon=True)
        self._ws_thread.start()
    async def _serve_forever(self):
        async with websockets.serve(self._ws_handler, self.host, self.port):
            try:
                logger.info(f"WebSocket服务器已启动：ws://{self.host}:{self.port}")
            except Exception:
                pass
            # 使用永不完成的Future维持事件循环
            await asyncio.Future()
    async def _ws_handler(self, websocket: WebSocketServerProtocol):
        """WS连接处理：注册/接收/注销。"""
        await self.register_client(websocket)
        try:
            async for message in websocket:
                await self.handle_message(websocket, message)
        finally:
            await self.unregister_client(websocket)
    def _schedule_on_ws_loop(self, coro):
        """在WS事件循环上调度协程，若循环不可用则回退为直接运行。"""
        try:
            loop = self._ws_loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, loop)
            else:
                asyncio.run(coro)
        except Exception:
            pass

    def _on_player_eliminated_bridge(self, player: Player) -> None:
        """在收到规则层淘汰信号时：
        - 调用进程层 on_player_eliminated 进行记录
        - 注销该席位的AI WebSocket客户端（若存在）
        """
        try:
            if self.process:
                try:
                    self.process.on_player_eliminated(player)
                except Exception:
                    pass
            # 改为在WS事件循环上调度，避免当前线程无事件循环导致异常
            self._schedule_on_ws_loop(self.unregister_ai_by_seat(player))
        except Exception:
            pass

    def _on_game_finished_bridge(self) -> None:
        """在收到规则层游戏结束信号时：
        - 调用进程层 on_game_finished 进行记录
        - 注销所有AI WebSocket客户端
        """
        try:
            if self.process:
                try:
                    self.process.on_game_finished()
                except Exception:
                    pass
            # 改为在WS事件循环上调度，避免当前线程无事件循环导致异常
            self._schedule_on_ws_loop(self.unregister_all_ai())
        except Exception:
            pass
            