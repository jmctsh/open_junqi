#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四国军棋游戏WebSocket服务器（优化：加载dotenv、统一错误响应、协议版本、AI接入）
"""

import asyncio
import json
import websockets
import os
import time
from typing import Dict, Set, Optional, Any, List
from websockets.server import WebSocketServerProtocol
import logging
from dotenv import load_dotenv, find_dotenv
import base64
import re
from enum import Enum
from game.game_logic import GameLogic, GameState
from game.piece import Player
from game.board import Position, CellType
from ai.agent import JunqiAgent
from ai.tts_client import DoubaoTTSClient
from server.strategies.scoring import score_legal_moves
from ai.prompt_themes import PERSONA_THEME_WEIGHTS

# 加载 .env（若存在）
load_dotenv(find_dotenv(), override=False)

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GameServer:
    """游戏服务器"""
    
    def __init__(self, host: str = "localhost", port: int = 8765):
        self.host = host
        self.port = port
        self.clients: Dict[WebSocketServerProtocol, Player] = {}
        self.game_logic = GameLogic()
        self.connected_players: Set[Player] = set()
        # 协议版本
        self.protocol_version: str = "1.0.0"
        # 新增：记录真人所控制的席位（单机模式为连接的客户端席位）
        self.human_seat: Optional[Player] = None
        # 初始化多玩家AI：改为“人物身份（persona）”维度的Agent，而非席位
        self.ai_agents_by_persona: Dict[str, Optional[JunqiAgent]] = {}
        for persona_key, suffix in [
            ("player1", "P1"),
            ("player2", "P2"),
            ("player3", "P3"),
        ]:
            api_key = os.environ.get(f"ARK_API_KEY_{suffix}") or os.environ.get("ARK_API_KEY")
            if not api_key:
                logger.warning(f"ARK_API_KEY_{suffix} 未配置，且未找到全局 ARK_API_KEY，人物 {persona_key} 的AI不可用。")
                self.ai_agents_by_persona[persona_key] = None
                continue
            try:
                self.ai_agents_by_persona[persona_key] = JunqiAgent(api_key=api_key)
                used = f"ARK_API_KEY_{suffix}" if os.environ.get(f"ARK_API_KEY_{suffix}") else "ARK_API_KEY"
                logger.info(f"[DEBUG] AI Agent 初始化成功（persona={persona_key}），使用密钥来源：{used}")
            except Exception as e:
                logger.warning(f"AI Agent 初始化失败（persona={persona_key}）：{e}。该人物AI不可用。")
                self.ai_agents_by_persona[persona_key] = None
        logger.info(
            f"[DEBUG] AI初始化完成：player1={'Y' if self.ai_agents_by_persona.get('player1') else 'N'}, "
            f"player2={'Y' if self.ai_agents_by_persona.get('player2') else 'N'}, "
            f"player3={'Y' if self.ai_agents_by_persona.get('player3') else 'N'}"
        )
        # 初始化 TTS 客户端（按人物身份分别配置）
        self.tts_clients_by_persona: Dict[str, Optional[DoubaoTTSClient]] = {}
        for persona_key, suffix in [
            ("player1", "P1"),
            ("player2", "P2"),
            ("player3", "P3"),
        ]:
            appid = os.environ.get(f"DOUBAO_TTS_APPID_{suffix}")
            access_token = os.environ.get(f"DOUBAO_TTS_ACCESS_TOKEN_{suffix}")
            cluster = os.environ.get(f"DOUBAO_TTS_CLUSTER_{suffix}")
            voice_type = os.environ.get(f"DOUBAO_TTS_VOICE_TYPE_{suffix}")
            secret_key = os.environ.get(f"DOUBAO_TTS_SECRET_KEY_{suffix}")
            if not all([appid, access_token, secret_key, cluster, voice_type]):
                logger.warning(f"TTS 凭据缺失（persona={persona_key}），该人物语音不可用。")
                self.tts_clients_by_persona[persona_key] = None
                continue
            try:
                self.tts_clients_by_persona[persona_key] = DoubaoTTSClient(
                    appid=appid,
                    access_token=access_token,
                    secret_key=secret_key,
                    cluster=cluster,
                    voice_type=voice_type,
                )
                logger.info(f"[DEBUG] TTS 初始化成功（persona={persona_key}）")
            except Exception as e:
                logger.warning(f"TTS 初始化失败（persona={persona_key}）：{e}")
                self.tts_clients_by_persona[persona_key] = None
        # 人物分配：席位(player_id) -> persona_key（如 "player1"/"player2"/"player3"）
        self.persona_assignments: Dict[int, str] = {}
        # 新增：聊天挂起与聊天历史（用于延迟广播与AI提示注入）
        self.pending_chats: Dict[Player, Optional[Dict[str, Any]]] = {}
        self.chat_history: List[Dict[str, Any]] = []
        # 新增：停止事件，用于统一关闭服务器
        self._stop_event = asyncio.Event()
        self._ai_running: bool = False

    def _sanitize_utterance(self, text: Optional[str]) -> Optional[str]:
        """去除LLM角色扮演常见的括号动作说明，保留纯文本，并限制长度。"""
        if not text or not isinstance(text, str):
            return None
        try:
            s = text
            s = re.sub(r"\([^)]*\)", "", s)  # 半角括号
            s = re.sub(r"（[^）]*）", "", s)   # 全角括号
            s = re.sub(r"[\[【][^\]】]*[\]】]", "", s)  # 方括号
            s = re.sub(r"\s+", " ", s).strip()
            return s[:15] if s else None
        except Exception:
            return text[:15]

    async def register_client(self, websocket: WebSocketServerProtocol) -> Optional[Player]:
        """注册新客户端"""
        # 单机模式：仅允许一个客户端连接，固定控制南方位
        if self.clients:
            await self.send_error(websocket, "单机模式仅允许一个客户端连接", code="ROOM_SINGLE")
            return None
        
        player = Player.PLAYER1  # 本地玩家固定为南方位
        self.clients[websocket] = player
        self.connected_players.add(player)
        # 记录真人席位
        self.human_seat = player
        logger.info("本地玩家已连接（南方位）")
        # 删除欢迎与加入广播，保持单机极简协议
        return player

    async def unregister_client(self, websocket: WebSocketServerProtocol):
        """注销客户端"""
        if websocket in self.clients:
            player = self.clients[websocket]
            del self.clients[websocket]
            self.connected_players.discard(player)
            logger.info("本地玩家断开连接")
            # 单机模式：不广播离开消息

    async def handle_message(self, websocket: WebSocketServerProtocol, message: str):
        """处理客户端消息"""
        try:
            data = json.loads(message)
            message_type = data.get("type")
            player = self.clients.get(websocket)
            logger.info(f"[DEBUG] 收到消息: type={message_type}, from={getattr(player, 'name', 'UNKNOWN')}")
            if not player:
                await self.send_error(websocket, "未注册的客户端", code="UNAUTHORIZED")
                return
            if message_type == "move_piece":
                await self.handle_move_piece(websocket, data, player)
            elif message_type == "place_piece":
                await self.handle_place_piece(websocket, data, player)
            elif message_type == "auto_layout":
                await self.handle_auto_layout(websocket, player)
            elif message_type == "start_game":
                await self.handle_start_game(websocket)
            elif message_type == "reset_game":
                await self.handle_reset_game(websocket)
            elif message_type == "get_game_state":
                await self.handle_get_game_state(websocket)
            elif message_type == "get_legal_moves":
                await self.handle_get_legal_moves(websocket, data)
            # 移除不符合规则的触发：禁止通过消息直接触发 AI
            # elif message_type == "ai_move":
            #     await self.handle_ai_move(websocket)
            elif message_type == "tts_synthesize":
                await self.handle_tts_synthesize(websocket, data)
            elif message_type == "set_persona_assignments":
                await self.handle_set_persona_assignments(websocket, data, player)
            elif message_type == "submit_chat":
                await self.handle_submit_chat(websocket, data, player)
            elif message_type == "skip_turn":
                # 跳过当前玩家回合并在切换后广播挂起聊天
                if self.game_logic.skip_turn():
                    await self.broadcast({
                        "type": "turn_skipped",
                        "player_id": player.value,
                        "game_state": self.get_public_game_state(),
                        "protocol_version": self.protocol_version,
                    })
                    await self._flush_pending_chat_for_player(player)
                    # 统一调度：若当前轮到AI，则触发一次 AI
                    await self._schedule_ai_if_needed(websocket)
            elif message_type == "surrender":
                # 当前玩家投降并在切换后广播挂起聊天
                if self.game_logic.surrender():
                    await self.broadcast({
                        "type": "player_surrendered",
                        "player_id": player.value,
                        "game_state": self.get_public_game_state(),
                        "protocol_version": self.protocol_version,
                    })
                    await self._flush_pending_chat_for_player(player)
                    # 统一调度：若当前轮到AI，则触发一次 AI
                    await self._schedule_ai_if_needed(websocket)
            elif message_type == "ping":
                await websocket.send(json.dumps({"type": "pong", "protocol_version": self.protocol_version}))
            else:
                await self.send_error(websocket, f"未知的消息类型: {message_type}", code="UNKNOWN_TYPE")
        except json.JSONDecodeError:
            await self.send_error(websocket, "无效的JSON格式", code="BAD_JSON")
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            await self.send_error(websocket, f"服务器错误: {str(e)}", code="SERVER_ERROR")

    async def handle_start_game(self, websocket: WebSocketServerProtocol):
        """处理开始游戏请求"""
        success = self.game_logic.start_game()
        await self.broadcast({
            "type": "game_started" if success else "start_game_failed",
            "success": success,
            "message": "游戏开始！" if success else "无法开始游戏，请检查所有玩家是否完成布局",
            "game_state": self.get_public_game_state(),
            "protocol_version": self.protocol_version,
        })
        logger.info(f"[DEBUG] 游戏开始，先手玩家：{self.game_logic.current_player.name}；单机模式：南方位为本地玩家，其他为AI")
        # 统一调度：仅在开始后进行一次AI/真人检测与触发
        if success:
            await self._schedule_ai_if_needed(websocket)

    async def _schedule_ai_if_needed(self, websocket: WebSocketServerProtocol):
        """统一AI调度：仅在对战阶段且当前玩家为AI时触发一次（非阻塞）。"""
        try:
            if self.game_logic.game_state != GameState.PLAYING:
                return
            if self._ai_running:
                logger.info("[DEBUG] AI执行中，跳过调度。")
                return
            cp = self.game_logic.current_player
            if self._is_seat_human(cp):
                logger.info(f"[DEBUG] 当前玩家 {cp.name} 为本地玩家，跳过AI调度。")
                return
            if not self._get_ai_agent_for_seat(cp):
                logger.warning(f"[DEBUG] 当前玩家 {cp.name} 未配置AI，跳过调度。")
                return
            # 统一由此入口触发AI执行（非阻塞）
            asyncio.create_task(self.handle_ai_move(websocket))
        except Exception as e:
            logger.error(f"[DEBUG] AI调度失败：{e}")


    async def handle_ai_move(self, websocket: WebSocketServerProtocol):
        """让当前玩家（若配置为AI）"""
        # 新增：仅在对战阶段执行AI，并防止并发触发
        if self.game_logic.game_state != GameState.PLAYING:
            await self.send_error(websocket, "当前不在对战阶段。", code="BAD_STATE")
            return
        if self._ai_running:
            logger.info("[DEBUG] 已有AI执行进行中，跳过本次触发。")
            return
        cp = self.game_logic.current_player
        if self._is_seat_human(cp):
            logger.info(f"[DEBUG] 当前玩家 {cp.name} 为本地玩家，跳过AI。")
            return
        agent = self._get_ai_agent_for_seat(cp)
        logger.info(f"[DEBUG] handle_ai_move: current_player={cp.name}, agent_ready={'Y' if agent else 'N'}")
        if not agent:
            await self.send_error(websocket, "当前玩家未配置AI或AI未初始化", code="AI_UNAVAILABLE")
            return
        self._ai_running = True
        # 构建状态与候选走法
        public_state = self.get_public_game_state(cp)
        # 完整合法走法列表（用于最终合法性校验与回退）
        legal_moves_full = self._build_legal_moves(cp)
        if not legal_moves_full:
            await self.send_error(websocket, "AI玩家无合法走法", code="NO_LEGAL_MOVE")
            self._ai_running = False
            return
        # 评分并筛选Top-N供LLM决策
        board = self.game_logic.board
        raw_pairs = board.enumerate_player_legal_moves(cp)
        scored_candidates = score_legal_moves(board, cp, raw_pairs, top_n=30)
        # 兜底重试：直到返回正确、合法的指令才结束（过程中不记录历史、不广播错误）
        attempts = 0
        max_attempts = 30
        while attempts < max_attempts:
            attempts += 1
            try:
                start_ts = time.perf_counter()
                action = await asyncio.to_thread(agent.choose_action, public_state, scored_candidates, cp.value)
                elapsed_ms = (time.perf_counter() - start_ts) * 1000
                logger.info(f"[DEBUG] Ark chat返回: 尝试#{attempts}, 耗时={elapsed_ms:.1f}ms, 类型={type(action)}")
                # 运行时JSON字段校验与容错日志
                if not isinstance(action, dict):
                    logger.warning(f"AI返回非字典结果（尝试#{attempts}）：{type(action)}；将继续重试")
                    await asyncio.sleep(0.05)
                    continue
                # 基础字段校验：使用 move.from / move.to
                move_obj = action.get("move")
                if not (isinstance(move_obj, dict) and "from" in move_obj and "to" in move_obj):
                    logger.warning(f"AI返回缺少 move/from/to（尝试#{attempts}），继续重试")
                    await asyncio.sleep(0.05)
                    continue
                fr_obj, to_obj = move_obj.get("from"), move_obj.get("to")
                if not (isinstance(fr_obj, dict) and isinstance(to_obj, dict)):
                    logger.warning(f"AI返回的 move.from/move.to 类型不正确（尝试#{attempts}），继续重试")
                    await asyncio.sleep(0.05)
                    continue
                # 坐标类型与范围校验
                try:
                    fr, fc = int(fr_obj.get("row")), int(fr_obj.get("col"))
                    tr, tc = int(to_obj.get("row")), int(to_obj.get("col"))
                except Exception:
                    logger.warning(f"AI返回坐标不可解析（尝试#{attempts}），继续重试。payload={action}")
                    await asyncio.sleep(0.05)
                    continue
                # 新增：在执行前再次确认当前回合未切换
                if self.game_logic.current_player != cp:
                    logger.info(f"[DEBUG] 当前轮已从 {cp.name} 切换为 {self.game_logic.current_player.name}，放弃本次AI执行。")
                    break
                # 使用最新完整列表进行合法性校验（只比对 from/to）
                legal_moves_current = self._build_legal_moves(cp)
                if not agent.is_move_in_legal(legal_moves_current, ((fr, fc), (tr, tc))):
                    logger.warning(f"AI返回非法走法（尝试#{attempts}），继续重试")
                    await asyncio.sleep(0.05)
                    continue
                # 额外兜底：直接检查棋盘 can_move
                from_pos = Position(fr, fc)
                to_pos = Position(tr, tc)
                if not self.game_logic.board.can_move(from_pos, to_pos):
                    logger.warning(f"AI走法在执行时不可移动（尝试#{attempts}），继续重试")
                    await asyncio.sleep(0.05)
                    continue
                # 执行
                success = self.game_logic.move_piece(from_pos, to_pos)
                if not success:
                    logger.warning(f"AI走法执行失败（尝试#{attempts}），将重试。move=(({fr},{fc})->({tr},{tc}))")
                    await asyncio.sleep(0.05)
                    continue
                # 生成AI发言（不阻塞执行），并发处理语音广播
                utterance = None
                try:
                    u = action.get("utterance")
                    utterance = self._sanitize_utterance(u)
                except Exception:
                    utterance = None
                # 目标清洗（允许字母数字、下划线、连字符，最多20字符）
                ut = action.get("utterance_target")
                sanitized_target = re.sub(r"[^a-zA-Z0-9_\-]", "", ut)[:20] if isinstance(ut, str) else None

                # 广播成功的走法（仅当执行成功时；不包含语音与聊天信息）
                await self.broadcast({
                    "type": "piece_moved",
                    "player_id": cp.value,
                    "from_pos": {"row": fr, "col": fc},
                    "to_pos": {"row": tr, "col": tc},
                    "game_state": self.get_public_game_state(),
                    "selected_id": action.get("selected_id") if isinstance(action.get("selected_id"), int) else None,
                    "protocol_version": self.protocol_version,
                })
                turn_idx_cur = len(self.game_logic.history.records)
                async def synth_and_broadcast():
                    try:
                        if not utterance:
                            return
                        client = self._get_tts_client_for_seat(cp)
                        tts_b64 = None
                        if client:
                            try:
                                audio_bytes = await asyncio.to_thread(client.synthesize, utterance)
                                tts_b64 = base64.b64encode(audio_bytes).decode("ascii")
                            except Exception as e:
                                logger.warning(f"TTS合成失败（{cp.name}）：{e}")
                        else:
                            logger.warning(f"TTS未配置（{cp.name}），跳过合成。")
                        # 写入聊天历史并广播（语音与文本）
                        try:
                            faction_map = {
                                Player.PLAYER1: "south",
                                Player.PLAYER2: "west",
                                Player.PLAYER3: "north",
                                Player.PLAYER4: "east",
                            }
                            self.chat_history.append({
                                "turn": turn_idx_cur,
                                "player_id": cp.value,
                                "player_faction": faction_map.get(cp),
                                "text": utterance,
                                "utterance_target": sanitized_target,
                            })
                            if len(self.chat_history) > 30:
                                self.chat_history = self.chat_history[-30:]
                        except Exception:
                            pass
                        await self.broadcast({
                            "type": "chat_message",
                            "player_id": cp.value,
                            "text": utterance,
                            "utterance_target": sanitized_target,
                            "tts_base64": tts_b64,
                            "protocol_version": self.protocol_version,
                        })
                    except Exception as e:
                        logger.warning(f"后台语音广播失败（{cp.name}）：{e}")

                try:
                    if utterance:
                        asyncio.create_task(synth_and_broadcast())
                except Exception:
                    pass

                # 广播挂起的聊天（若有）
                await self._flush_pending_chat_for_player(cp)
                break
            except Exception as e:
                logger.warning(f"AI决策解析失败（尝试#{attempts}）：{e}，继续重试")
                await asyncio.sleep(0.05)
        # 若超出最大尝试次数，停止并返回错误，避免无限重试
        if attempts >= max_attempts:
            await self.send_error(websocket, "AI决策尝试次数过多，已停止", code="AI_RETRY_EXHAUSTED")
            self._ai_running = False
            return
        # 标志复位：本次AI已完成一次有效走子或已停止重试
        self._ai_running = False
        # 连续AI调度：统一检测并在需要时触发
        await self._schedule_ai_if_needed(websocket)


    # 通用：确保字典键与枚举值可被 JSON 序列化
    def _json_safe(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            safe_dict: Dict[Any, Any] = {}
            for k, v in obj.items():
                if isinstance(k, Enum):
                    key = k.value
                elif isinstance(k, (str, int, float, bool)) or k is None:
                    key = k
                else:
                    key = str(k)
                safe_dict[key] = self._json_safe(v)
            return safe_dict
        elif isinstance(obj, list):
            return [self._json_safe(x) for x in obj]
        if isinstance(obj, tuple):
            return [self._json_safe(x) for x in obj]
        if isinstance(obj, Enum):
            return obj.value
        return obj

    # === 席位/人物辅助方法（统一真人/AI判断与资源获取）===
    def _is_seat_human(self, seat: Player) -> bool:
        """判断该席位是否为真人控制（单机模式下为已连接的本地席位）。"""
        try:
            return self.human_seat == seat
        except Exception:
            return False

    def _get_persona_for_seat(self, seat: Player) -> Optional[str]:
        """根据席位返回其绑定的人物身份（persona key），若未分配则返回None。"""
        try:
            return self.persona_assignments.get(int(seat.value))
        except Exception:
            return None

    def _get_ai_agent_for_seat(self, seat: Player) -> Optional[JunqiAgent]:
        """获取该席位对应人物的AI代理。"""
        persona = self._get_persona_for_seat(seat)
        if not isinstance(persona, str) or not persona:
            return None
        return self.ai_agents_by_persona.get(persona)

    def _get_tts_client_for_seat(self, seat: Player) -> Optional[DoubaoTTSClient]:
        """获取该席位对应人物的TTS客户端。"""
        persona = self._get_persona_for_seat(seat)
        if not isinstance(persona, str) or not persona:
            return None
        return self.tts_clients_by_persona.get(persona)

    async def broadcast(self, message: Dict, exclude: Optional[WebSocketServerProtocol] = None):
        """广播消息给所有客户端"""
        if not self.clients:
            return
        message_str = json.dumps(self._json_safe(message))
        disconnected = []
        for websocket in self.clients:
            if websocket != exclude:
                try:
                    await websocket.send(message_str)
                except websockets.exceptions.ConnectionClosed:
                    disconnected.append(websocket)
        # 清理断开的连接
        for websocket in disconnected:
            await self.unregister_client(websocket)


    async def _flush_pending_chat_for_player(self, player: Player):
        """若该玩家有挂起聊天，则广播并记录历史，然后清空挂起。"""
        try:
            pending = self.pending_chats.get(player)
            if not pending:
                return
            text = pending.get("text")
            target = pending.get("utterance_target")
            tts_b64 = pending.get("tts_base64")
            # 记录到聊天历史（按当前回合索引）
            try:
                faction_map = {
                    Player.PLAYER1: "south",
                    Player.PLAYER2: "west",
                    Player.PLAYER3: "north",
                    Player.PLAYER4: "east",
                }
                turn_idx = len(self.game_logic.history.records)
                self.chat_history.append({
                    "turn": turn_idx,
                    "player_id": player.value,
                    "player_faction": faction_map.get(player),
                    "text": text,
                    "utterance_target": target,
                })
                if len(self.chat_history) > 30:
                    self.chat_history = self.chat_history[-30:]
            except Exception:
                pass
            # 广播聊天消息给所有客户端
            await self.broadcast({
                "type": "chat_message",
                "player_id": player.value,
                "text": text,
                "utterance_target": target,
                "tts_base64": tts_b64,
                "protocol_version": self.protocol_version,
            })
        finally:
            # 清空挂起
            self.pending_chats[player] = None



    async def handle_client(self, websocket: WebSocketServerProtocol, path: str):
        """处理客户端连接"""
        player = await self.register_client(websocket)
        if not player:
            return
        try:
            async for message in websocket:
                await self.handle_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister_client(websocket)

    async def start_server(self):
        """启动服务器"""
        logger.info(f"启动游戏服务器 {self.host}:{self.port}")
        async with websockets.serve(self.handle_client, self.host, self.port):
            logger.info("游戏服务器已启动，等待连接...")
            await self._stop_event.wait()
        logger.info("游戏服务器已停止")

    async def stop_server(self):
        """优雅停止服务器: 触发内部停止事件并关闭所有连接"""
        try:
            if not self._stop_event.is_set():
                self._stop_event.set()
            # 通知所有客户端并关闭连接
            if self.clients:
                await self.broadcast({
                    "type": "server_stopping",
                    "protocol_version": self.protocol_version,
                })
                # 逐个关闭连接
                to_close = list(self.clients.keys()) if isinstance(self.clients, dict) else list(self.clients)
                for ws in to_close:
                    try:
                        await ws.close()
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"stop_server 过程中出错: {e}")
        finally:
            logger.info("stop_server 调用完成")

    async def send_error(self, websocket: WebSocketServerProtocol, message: str, code: str = "ERR", extra: Optional[Dict[str, Any]] = None):
        """统一错误响应"""
        payload = {"type": "error", "code": code, "message": message, "protocol_version": self.protocol_version}
        if extra:
            payload.update(extra)
        await websocket.send(json.dumps(payload))

    async def handle_get_game_state(self, websocket: WebSocketServerProtocol):
        """返回公开游戏状态（按请求客户端视角）"""
        viewer = self.clients.get(websocket)
        await websocket.send(json.dumps(self._json_safe({
            "type": "game_state",
            "state": self.get_public_game_state(viewer),
            "protocol_version": self.protocol_version,
        })))

    async def handle_set_persona_assignments(self, websocket: WebSocketServerProtocol, data: Dict[str, Any], player: Player):
        """接收并存储布局阶段的人物分配：{"assignments": {"1": "player1", "2": "player3", "3": "player2"}}"""
        assignments = data.get("assignments")
        if not isinstance(assignments, dict):
            await self.send_error(websocket, "assignments必须为字典", code="BAD_PARAM")
            return
        allowed_personas = set(PERSONA_THEME_WEIGHTS.keys())
        updated: Dict[int, str] = {}
        for k, v in assignments.items():
            try:
                pid = int(k)
            except (TypeError, ValueError):
                continue
            if pid not in (Player.PLAYER1.value, Player.PLAYER2.value, Player.PLAYER3.value, Player.PLAYER4.value):
                # 仅处理有效席位
                continue
            if isinstance(v, str) and v in allowed_personas:
                updated[pid] = v
        if not updated:
            await self.send_error(websocket, "未提供有效的人物分配", code="BAD_PARAM")
            return
        # 更新并确认
        self.persona_assignments.update(updated)
        await websocket.send(json.dumps({
            "type": "set_persona_assignments_result",
            "success": True,
            "persona_assignments": self.persona_assignments,
            "protocol_version": self.protocol_version,
        }))

    async def handle_submit_chat(self, websocket: WebSocketServerProtocol, data: Dict[str, Any], player: Player):
        """处理提交聊天请求：支持挂起（延迟广播）或立即广播。
        data: {
          text: str,                  # 必填，聊天文本
          utterance_target?: str,     # 可选，目标ID或别名
          with_tts?: bool,            # 可选，是否生成TTS音频
          immediate?: bool            # 可选，是否立即广播（默认False：挂起，等待切回合或走子后统一广播）
        }
        """
        try:
            raw_text = data.get("text")
            if not isinstance(raw_text, str) or not raw_text.strip():
                await self.send_error(websocket, "text必须为非空字符串", code="BAD_PARAM")
                return
            text = self._sanitize_utterance(raw_text)
            ut = data.get("utterance_target")
            sanitized_target = re.sub(r"[^a-zA-Z0-9_\-]", "", ut)[:20] if isinstance(ut, str) else None
            with_tts = bool(data.get("with_tts"))
            tts_b64 = None
            if with_tts:
                client = self._get_tts_client_for_seat(player)
                if client:
                    try:
                        audio_bytes = await asyncio.to_thread(client.synthesize, text)
                        tts_b64 = base64.b64encode(audio_bytes).decode("ascii")
                    except Exception as e:
                        logger.warning(f"TTS合成失败（{player.name}）：{e}")
                else:
                    logger.warning(f"TTS未配置（{player.name}），跳过合成。")
            payload = {
                "text": text,
                "utterance_target": sanitized_target,
                "tts_base64": tts_b64,
            }
            immediate = bool(data.get("immediate"))
            if immediate:
                # 立即广播并写入历史
                try:
                    faction_map = {
                        Player.PLAYER1: "south",
                        Player.PLAYER2: "west",
                        Player.PLAYER3: "north",
                        Player.PLAYER4: "east",
                    }
                    turn_idx = len(self.game_logic.history.records)
                    self.chat_history.append({
                        "turn": turn_idx,
                        "player_id": player.value,
                        "player_faction": faction_map.get(player),
                        "text": text,
                        "utterance_target": sanitized_target,
                    })
                    if len(self.chat_history) > 30:
                        self.chat_history = self.chat_history[-30:]
                except Exception:
                    pass
                await self.broadcast({
                    "type": "chat_message",
                    "player_id": player.value,
                    "text": text,
                    "utterance_target": sanitized_target,
                    "tts_base64": tts_b64,
                    "protocol_version": self.protocol_version,
                })
                # 清空挂起
                self.pending_chats[player] = None
                await websocket.send(json.dumps({
                    "type": "submit_chat_result",
                    "success": True,
                    "immediate": True,
                    "protocol_version": self.protocol_version,
                }))
            else:
                # 写入挂起，待切回合或走子后统一广播
                self.pending_chats[player] = payload
                await websocket.send(json.dumps({
                    "type": "submit_chat_result",
                    "success": True,
                    "pending": True,
                    "protocol_version": self.protocol_version,
                }))
        except Exception as e:
            await self.send_error(websocket, f"提交聊天失败: {e}", code="CHAT_FAILED")

    async def handle_tts_synthesize(self, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """处理TTS合成：优先使用请求者席位对应的TTS客户端。支持指定player_id。"""
        try:
            raw_text = data.get("text")
            if not isinstance(raw_text, str) or not raw_text.strip():
                await self.send_error(websocket, "text必须为非空字符串", code="BAD_PARAM")
                return
            text = self._sanitize_utterance(raw_text) or raw_text.strip()[:15]
            # 选择席位：优先当前连接的玩家，其次 data.player_id
            player = self.clients.get(websocket)
            if not player:
                pid = data.get("player_id")
                try:
                    if pid is not None:
                        pid_int = int(pid)
                        player = Player(pid_int)
                except Exception:
                    player = None
            if not player:
                await self.send_error(websocket, "无法确定TTS席位", code="BAD_PARAM")
                return
            client = self._get_tts_client_for_seat(player)
            if not client:
                await self.send_error(websocket, f"TTS未配置（{player.name}）", code="TTS_UNAVAILABLE")
                return
            try:
                audio_bytes = await asyncio.to_thread(client.synthesize, text)
                tts_b64 = base64.b64encode(audio_bytes).decode("ascii")
            except Exception as e:
                await self.send_error(websocket, f"TTS合成失败：{e}", code="TTS_FAILED")
                return
            await websocket.send(json.dumps({
                "type": "tts_result",
                "success": True,
                "tts_base64": tts_b64,
                "protocol_version": self.protocol_version,
            }))
        except Exception as e:
            await self.send_error(websocket, f"TTS请求失败: {e}", code="TTS_FAILED")

    def _build_legal_moves(self, player: Player) -> List[Dict[str, Dict[str, int]]]:
        """将棋盘的合法走法枚举转换为 JSON 友好结构"""
        pairs = self.game_logic.board.enumerate_player_legal_moves(player)
        result: List[Dict[str, Dict[str, int]]] = []
        for fr, to in pairs:
            result.append({
                "from": {"row": fr.row, "col": fr.col},
                "to": {"row": to.row, "col": to.col},
            })
        return result

    async def handle_get_legal_moves(self, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """返回指定玩家或当前玩家的合法走法列表"""
        player_id = data.get("player_id")
        player = Player(player_id) if player_id else self.game_logic.current_player
        # 优化：支持按 piece_id 过滤该棋子的合法走法
        piece_id = data.get("piece_id")
        if piece_id:
            # 查找该棋子位置（仅限所属玩家）
            from_pos = None
            for position, cell in self.game_logic.board.cells.items():
                if cell.piece and cell.piece.piece_id == piece_id and cell.piece.player == player:
                    from_pos = position
                    break
            if from_pos:
                pairs = []
                for _, to in self.game_logic.board.enumerate_player_legal_moves(player):
                    # enumerate 返回所有棋子对；此处只筛选 from_pos 对应项
                    pass
                # 直接以 can_move 生成该起点的合法目的地
                moves = []
                # 邻接与铁路能力由棋盘规则处理
                candidates = set(self.game_logic.board.get_adjacent_positions(from_pos))
                cell = self.game_logic.board.get_cell(from_pos)
                if cell and cell.cell_type == CellType.RAILWAY:
                    if cell.piece and cell.piece.is_engineer():
                        candidates |= self.game_logic.board.get_railway_connected_positions(from_pos)
                    else:
                        candidates |= self.game_logic.board.get_railway_straight_reachable_positions(from_pos)
                for to_pos in candidates:
                    if self.game_logic.board.can_move(from_pos, to_pos):
                        moves.append({
                            "from": {"row": from_pos.row, "col": from_pos.col},
                            "to": {"row": to_pos.row, "col": to_pos.col},
                        })
            else:
                moves = []
        else:
            moves = self._build_legal_moves(player)
        await websocket.send(json.dumps({
            "type": "legal_moves",
            "player_id": player.value,
            "moves": moves,
            "protocol_version": self.protocol_version,
        }))

    def _find_piece_position_by_id(self, piece_id: str) -> Optional[Position]:
        """根据唯一棋子ID查找其当前位置。"""
        for position, cell in self.game_logic.board.cells.items():
            if cell.piece and cell.piece.piece_id == piece_id:
                return position
        return None

    async def handle_move_piece(self, websocket: WebSocketServerProtocol, data: Dict, player: Player):
        """处理移动棋子请求（支持坐标或按 piece_id 指定起点）"""
        try:
            piece_id = data.get("piece_id")
            if piece_id:
                from_pos = self._find_piece_position_by_id(piece_id)
                if not from_pos:
                    await self.send_error(websocket, "未找到指定棋子", code="PIECE_NOT_FOUND")
                    return
                # 若同时提供了 from_row/from_col，则进行一致性校验
                if "from_row" in data and "from_col" in data:
                    fr, fc = int(data["from_row"]), int(data["from_col"])
                    if from_pos.row != fr or from_pos.col != fc:
                        await self.send_error(websocket, "起点与棋子ID不一致", code="FROM_MISMATCH")
                        return
                # 如果提供了嵌套的 to 对象，则优先使用
                if "to" in data and isinstance(data["to"], dict):
                    to_obj = data["to"]
                    to_pos = Position(int(to_obj["row"]), int(to_obj["col"]))
                else:
                    to_pos = Position(int(data["to_row"]), int(data["to_col"]))
            else:
                # 支持 move 对象、嵌套的 from/to 结构或扁平参数
                if "move" in data and isinstance(data["move"], dict):
                    m = data["move"]
                    if "from" in m and "to" in m and isinstance(m["from"], dict) and isinstance(m["to"], dict):
                        fr_obj = m["from"]
                        to_obj = m["to"]
                        from_pos = Position(int(fr_obj["row"]), int(fr_obj["col"]))
                        to_pos = Position(int(to_obj["row"]), int(to_obj["col"]))
                    else:
                        # 回退：允许扁平 move.from_row 等
                        from_pos = Position(int(m.get("from_row")), int(m.get("from_col")))
                        to_pos = Position(int(m.get("to_row")), int(m.get("to_col")))
                elif "from" in data and "to" in data and isinstance(data["from"], dict) and isinstance(data["to"], dict):
                    fr_obj = data["from"]
                    to_obj = data["to"]
                    from_pos = Position(int(fr_obj["row"]), int(fr_obj["col"]))
                    to_pos = Position(int(to_obj["row"]), int(to_obj["col"]))
                else:
                    from_pos = Position(int(data["from_row"]), int(data["from_col"]))
                    to_pos = Position(int(data["to_row"]), int(data["to_col"]))
            # 额外安全校验：仅允许当前玩家或测试模式移动
            cell = self.game_logic.board.get_cell(from_pos)
            if not cell or not cell.piece:
                await self.send_error(websocket, "起点无棋子", code="NO_PIECE_AT_FROM")
                return
            if (not self.game_logic.testing_mode) and (cell.piece.player != self.game_logic.current_player or cell.piece.player != player):
                await self.send_error(websocket, "不能移动非当前玩家的棋子", code="FORBIDDEN")
                return
            success = self.game_logic.move_piece(from_pos, to_pos)
            response = {
                "type": "move_result",
                "success": success,
                "from_pos": {"row": from_pos.row, "col": from_pos.col},
                "to_pos": {"row": to_pos.row, "col": to_pos.col},
                "protocol_version": self.protocol_version,
            }
            if success:
                # 广播移动结果给所有玩家（仅公开视图）
                await self.broadcast({
                    "type": "piece_moved",
                    "player_id": player.value,
                    "from_pos": {"row": from_pos.row, "col": from_pos.col},
                    "to_pos": {"row": to_pos.row, "col": to_pos.col},
                    "game_state": self.get_public_game_state(),
                    "protocol_version": self.protocol_version,
                })
                # 走子完成后，若该玩家有挂起聊天，统一广播并记录历史
                await self._flush_pending_chat_for_player(player)
                # 统一调度：若当前轮到AI，则触发一次 AI
                await self._schedule_ai_if_needed(websocket)
            else:
                await websocket.send(json.dumps(response))
        except KeyError as e:
            await self.send_error(websocket, f"缺少必要参数: {e}", code="MISSING_PARAM")
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            await self.send_error(websocket, f"服务器错误: {str(e)}", code="SERVER_ERROR")

    def get_public_game_state(self, viewer: Optional[Player] = None) -> Dict[str, Any]:
        """构建面向客户端/AI的公开游戏状态（不泄露隐藏棋子身份）。
        viewer: 视角玩家（可选）。若提供，则其本方棋子类型可见；否则仅在 testing_mode 或 piece.visible 时可见。
        """
        gl = self.game_logic
        faction_map = {
            Player.PLAYER1: "south",
            Player.PLAYER2: "west",
            Player.PLAYER3: "north",
            Player.PLAYER4: "east",
        }
        # 棋盘公开信息：仅输出有子的格子；棋子身份遵循可见规则
        board_pieces: List[Dict[str, Any]] = []
        try:
            for position, cell in gl.board.cells.items():
                if not cell.piece:
                    continue
                can_see = (
                    gl.testing_mode
                    or cell.piece.visible
                    or (viewer is not None and cell.piece.player == viewer)
                )
                piece_info: Dict[str, Any] = {
                    "player_id": cell.piece.player.value,
                    "piece_id": cell.piece.piece_id,
                }
                if can_see and getattr(cell.piece, "piece_type", None) is not None:
                    piece_info["piece_type"] = cell.piece.piece_type.name
                board_pieces.append({
                    "row": position.row,
                    "col": position.col,
                    "piece": piece_info,
                })
        except Exception:
            # 若棋盘遍历意外失败，提供一个保守占位结构，避免中断流程
            board_pieces = []
        board_state: Dict[str, Any] = {
            "rows": gl.board.rows,
            "cols": gl.board.cols,
            "pieces": board_pieces,
        }
        # 各玩家是否完成布局
        setup_map: Dict[int, bool] = {p.value: bool(gl.setup_complete.get(p, False)) for p in Player}
        # 队伍划分（南+北；东+西）
        teams = {
            "south_north": [Player.PLAYER1.value, Player.PLAYER3.value],
            "east_west": [Player.PLAYER2.value, Player.PLAYER4.value],
        }
        # 人物分配（可能为空），确保键值可序列化
        persona_assignments_safe = self._json_safe(self.persona_assignments)
        # 席位是否为真人控制的映射（供客户端/AI避免硬编码本地席位）
        is_human_map = {p.value: bool(self._is_seat_human(p)) for p in Player}
        return {
            "state": gl.game_state.value,
            "current_player": gl.current_player.value,
            "current_player_faction": faction_map.get(gl.current_player),
            "faction_names": {
                "south": "小红",
                "west": "淡淡色",
                "north": "小绿",
                "east": "橙猫猫",
            },
            "teams": teams,
            "setup_complete": setup_map,
            "is_human_map": is_human_map,
            "eliminated_players": [p.value for p in gl.eliminated_players],
            "board": board_state,
            "history": gl.history.to_list(),
            "persona_assignments": persona_assignments_safe,
        }

if __name__ == "__main__":
    # 直接运行该文件以启动服务器
    import asyncio
    try:
        server = GameServer()
        asyncio.run(server.start_server())
    except KeyboardInterrupt:
        # 支持 Ctrl+C 退出
        pass
            