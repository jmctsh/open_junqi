from typing import Dict, Optional, List, Callable
import logging
from game.piece import Player
from typing import Any
# 新增：进程层直接接入规则层与AI层
from game.game_logic import GameLogic
from ai.agent import JunqiAgent
from server.strategies.scoring import score_legal_moves
# 新增：TTS客户端与临时文件支持
import os
import tempfile
from ai.tts_client import DoubaoTTSClient
# 新增：后台线程与时间控制
import threading
import time
import json


class GameProcess:
    """单局游戏进程管理：记录席位与AI人物（persona）的对应关系，并在开局时生成方位-人物键值对。
    人物差异仅体现在 ai/prompt_themes.py 的主题权重配比（player1/player2/player3）。
    """
    def __init__(self) -> None:
        self._started: bool = False
        # 席位(player_id: 1..4) -> persona_key("player1"/"player2"/"player3")
        self._seat_to_persona: Dict[int, str] = {}
        # 供日志/调试使用的方位-人物三组对应（示例："北-player3"、"东-player1"、"西-player2")
        self._faction_persona_pairs: List[str] = []
        # 允许的人物键
        self._allowed_personas = {"player1", "player2", "player3"}
        # 席位到方位（英文）
        self._seat_to_faction = {1: "south", 2: "west", 3: "north", 4: "east"}

        self._logger = logging.getLogger(__name__)
        # 当前回合上下文：席位与方位（供调度与外部查询使用）
        self._current_turn_player: Optional[Player] = None
        self._current_turn_faction: Optional[str] = None
        # 新增：规则层与AI层依赖
        self._game_logic: Optional[GameLogic] = None
        self._agent: Optional[JunqiAgent] = None
        self._ai_action_consumer: Optional[Callable[[Dict[str, Any]], None]] = None
        # 固定阵营称呼（供 LLM 验证 utterance_target）
        self._faction_names: Dict[str, str] = {"south": "小红", "west": "淡淡色", "north": "小绿", "east": "橙猫猫"}
        # 新增：待广播的模型utterance缓存（按席位存储），以及UI层消费回调
        self._pending_utterances: Dict[int, Dict[str, Any]] = {}
        self._broadcast_consumer: Optional[Callable[[Dict[str, Any]], None]] = None
        # 最低响应时间控制：记录最近一次 LLM 调用时间戳
        self._last_llm_call_ts: Optional[float] = None
        # 新增：AI后台调度线程状态
        self._ai_worker_thread: Optional[threading.Thread] = None
        self._ai_worker_busy: bool = False
        # 新增：当后台线程繁忙时，排队一次后续调度，避免错过回合
        self._pending_dispatch: bool = False
        self._pending_prev_player: Optional[Player] = None

    def set_game_logic(self, gl: GameLogic) -> None:
        """注册规则层 GameLogic，进程层可据此构建 public_state 与合法走法。"""
        self._game_logic = gl

    def attach_agent(self, agent: JunqiAgent) -> None:
        """注册 AI 代理（JunqiAgent）。"""
        self._agent = agent

    def set_ai_action_consumer(self, consumer: Callable[[Dict[str, Any]], None]) -> None:
        """注册 AI 输出消费回调：用于上层应用执行走子或渲染。"""
        self._ai_action_consumer = consumer

    # 新增：注册广播消费回调（UI层播放语音与展示文本）
    def set_broadcast_consumer(self, consumer: Callable[[Dict[str, Any]], None]) -> None:
        self._broadcast_consumer = consumer

    def start_game(self, assignments: Dict[int, str]) -> None:
        """在游戏开始时冻结本局的席位-人物身份映射，并生成三组方位-人物对应关系。"""
        self._seat_to_persona = {}
        # 仅接受有效席位与已注册人物
        for pid, persona in assignments.items():
            try:
                ipid = int(pid)
            except Exception:
                continue
            if ipid not in (1, 2, 3, 4):
                continue
            if persona in self._allowed_personas:
                self._seat_to_persona[ipid] = persona
        # 生成“北/东/西-人物”三组对应（南为本地真人，通常不参与AI人物三组）
        pairs: List[str] = []
        for ipid, persona in self._seat_to_persona.items():
            faction = self._seat_to_faction.get(ipid)
            if faction in ("east", "west", "north") and persona in self._allowed_personas:
                pairs.append(f"{self._faction_zh(faction)}-{persona}")
        self._faction_persona_pairs = pairs
        self._started = True

    def _faction_zh(self, faction: str) -> str:
        mapping = {"south": "南", "west": "西", "north": "北", "east": "东"}
        return mapping.get(faction, faction)

    def get_persona_for_seat(self, seat: Player) -> Optional[str]:
        if not self._started:
            return None
        try:
            return self._seat_to_persona.get(int(seat.value))
        except Exception:
            return None

    def get_faction_persona_pairs(self) -> List[str]:
        """返回本局的三组方位-人物对应（示例：["北-player3", "东-player1", "西-player2"]）。"""
        return list(self._faction_persona_pairs)

    def get_seat_to_persona(self) -> Dict[int, str]:
        """返回 seat->persona 的映射副本，用于调试或外部查询。"""
        return dict(self._seat_to_persona)

    def get_current_turn_player(self) -> Optional[Player]:
        """返回当前回合的席位（Player 枚举）。"""
        return self._current_turn_player

    def get_current_turn_faction(self) -> Optional[str]:
        """返回当前回合的方位字符串（"south"/"west"/"north"/"east"），若未知则为 None。"""
        return self._current_turn_faction

    def get_current_turn_persona(self) -> Optional[str]:
        """返回当前回合席位对应的人格键（player1/player2/player3）。"""
        return self.get_persona_for_seat(self._current_turn_player)


    def is_current_turn_ai(self) -> bool:
        """判断当前回合是否为AI席位。"""
        seat = self._current_turn_player
        if seat is None:
            return False
        persona = self.get_persona_for_seat(seat)
        return persona in self._allowed_personas

    # 新增：构建供 LLM 使用的 public_state（仅依赖公开信息）
    def _build_public_state(self) -> Dict[str, Any]:
        gl = self._game_logic
        if gl is None:
            return {}
        try:
            board_cells = []
            piece_id_local_map = []
            # 统一构建 id_coords：仅为“当前席位”的棋子附加 face（该席位可见自己的棋面）
            current_seat = self._current_turn_player
            id_coords_map: Dict[str, Any] = {}
            for position, cell in gl.board.cells.items():
                cell_type = getattr(getattr(cell, "cell_type", None), "name", "NORMAL") if cell else "NORMAL"
                piece_info = None
                if cell and getattr(cell, "piece", None):
                    pid = cell.piece.piece_id
                    piece_info = {
                        "player_id": cell.piece.player.value,
                        "piece_id": pid,
                        "visible": bool(getattr(cell.piece, "visible", False)),
                        "can_move": bool(cell.piece.can_move()),
                    }
                    if pid:
                        lr, lc = gl._get_local_coords(position, cell.piece.player)
                        piece_id_local_map.append({
                            "piece_id": pid,
                            "player_id": cell.piece.player.value,
                            "local_row": lr,
                            "local_col": lc,
                        })
                        # 统一公开坐标映射（仅对当前席位的棋子附加 face）
                        entry: Dict[str, Any] = {"row": lr, "col": lc}
                        if current_seat is not None and cell.piece.player == current_seat:
                            try:
                                entry["face"] = cell.piece.piece_type.value
                            except Exception:
                                pass
                        id_coords_map[str(pid)] = entry
                board_cells.append({
                    "row": position.row,
                    "col": position.col,
                    "cell_type": cell_type,
                    "piece": piece_info,
                })
        except Exception:
            board_cells = []
            piece_id_local_map = []
            id_coords_map = {}
        setup_map: Dict[int, bool] = {p.value: bool(gl.setup_complete.get(p, False)) for p in Player}
        teams = {
            "south_north": [Player.PLAYER1.value, Player.PLAYER3.value],
            "east_west": [Player.PLAYER2.value, Player.PLAYER4.value],
        }
        # persona_assignments：仅注入已分配的人格席位
        persona_assignments: Dict[int, str] = {}
        for s, persona in self._seat_to_persona.items():
            persona_assignments[int(s)] = str(persona)
        public_state: Dict[str, Any] = {
            "state": gl.game_state.value,
            "current_player": (self._current_turn_player.value if self._current_turn_player else gl.current_player.value),
            "current_player_faction": self._current_turn_faction,
            "faction_names": dict(self._faction_names),
            "teams": teams,
            "setup_complete": setup_map,
            "eliminated_players": [p.value for p in gl.eliminated_players],
            "board": {
                "rows": gl.board.rows,
                "cols": gl.board.cols,
                "cells": board_cells,
                },
                # 统一坐标映射入口（仅此一处）
                "id_coords": id_coords_map,
                "history": gl.history.to_list(),
                "persona_assignments": persona_assignments,
            }
        return public_state

    # 新增：构建当前席位玩家状态（仅自己的棋面与本地坐标）
    def _get_player_state(self, player: Player) -> Dict[str, Any]:
        gl = self._game_logic
        if gl is None:
            return {}
        own_pieces: List[Dict[str, Any]] = []
        for position, cell in gl.board.cells.items():
            if cell and getattr(cell, "piece", None) and cell.piece.player == player:
                pid = cell.piece.piece_id
                lr, lc = gl._get_local_coords(position, player)
                own_pieces.append({
                    "piece_id": pid,
                    "piece_type": cell.piece.piece_type.value,
                    "local_row": lr,
                    "local_col": lc,
                    "controllable": True,
                })
        return {
            "for_player_id": player.value,
            "own_pieces": own_pieces,
        }

    # 新增：生成并评分合法走法（供 LLM 选择）
    def _get_legal_moves_scored(self, player: Player) -> List[Dict[str, Any]]:
        gl = self._game_logic
        if gl is None:
            return []
        raw = gl.board.enumerate_player_legal_moves(player)
        try:
            return score_legal_moves(gl.board, player, raw, top_n=10)
        except Exception:
            return []

    # === 进程信号对接（由规则层触发，上层在初始化时注册AI调度回调） ===

    def _schedule_ai_safe(self) -> None:
        try:
            # 仅在当前为AI席位时触发调度；真人席位直接跳过
            if not self.is_current_turn_ai():
                try:
                    self._logger.debug("[DEBUG] 当前为真人回合，跳过AI调度。")
                except Exception:
                    pass
                return
            # 优先使用内置的 JunqiAgent 调度链路（若已注册依赖）
            if self._agent and self._game_logic:
                try:
                    self._logger.debug(
                        f"[DEBUG] 触发AI调度：seat={self._current_turn_player}, faction={self._current_turn_faction}, persona={self.get_current_turn_persona()}"
                    )
                except Exception:
                    pass
                public_state = self._build_public_state()
                legal_moves = self._get_legal_moves_scored(self._current_turn_player) if self._current_turn_player else []
                # 无候选直接跳过
                if not legal_moves:
                    try:
                        self._logger.debug("[DEBUG] 当前无合法走法候选，跳过模型调用。")
                    except Exception:
                        pass
                    return
                player_id = int(self._current_turn_player.value)
                # 统一数据源：不再注入独立玩家状态块
                # 不再构造/传递独立的 player_state，统一通过 public_state.id_coords 的 face 提示（仅真人棋子）
                player_state = self._get_player_state(self._current_turn_player) if self._current_turn_player else None
                # player_state 已废弃，不再使用
                # 最低响应时间控制：首次3秒；后续与上次调用不足5秒则补足等待
                try:
                    import time
                    wait_secs = 0.0
                    if self._last_llm_call_ts is None:
                        wait_secs = 3.0
                    else:
                        elapsed = time.time() - self._last_llm_call_ts
                        if elapsed < 5.0:
                            wait_secs = 5.0 - elapsed
                    if wait_secs > 0:
                        try:
                            self._logger.debug(f"[DEBUG] 最低响应时间控制：等待 {wait_secs:.1f}s 后再调用LLM")
                        except Exception:
                            pass
                        time.sleep(wait_secs)
                except Exception:
                    pass
                try:
                    action = self._agent.choose_action(
                        public_state,
                        legal_moves,
                        player_id,
                        chat_history=self._game_logic.history if self._game_logic else None,
                        chat_history_recorder=self._game_logic.history if self._game_logic else None,
                    )
                    # 记录本次调用时间戳
                    try:
                        import time
                        self._last_llm_call_ts = time.time()
                    except Exception:
                        pass
                    # 新增：业务日志（完整 action 与关键上下文）
                    try:
                        biz_logger = logging.getLogger("junqi_ai.biz")
                        biz_logger.setLevel(logging.INFO)
                        biz_logger.propagate = False
                        # 业务日志文件处理器（避免重复添加）
                        try:
                            import os
                            logs_dir = os.path.join(os.getcwd(), "logs")
                            os.makedirs(logs_dir, exist_ok=True)
                            biz_log_path = os.path.join(logs_dir, "biz.log")
                            need_handler = True
                            for h in biz_logger.handlers:
                                if isinstance(h, logging.FileHandler):
                                    try:
                                        if getattr(h, "baseFilename", None) == biz_log_path:
                                            need_handler = False
                                            break
                                    except Exception:
                                        continue
                            if need_handler:
                                fh = logging.FileHandler(biz_log_path, encoding="utf-8")
                                fh.setLevel(logging.INFO)
                                fh.setFormatter(logging.Formatter('%(asctime)s\t%(message)s'))
                                biz_logger.addHandler(fh)
                        except Exception:
                            pass
                        # 简要裁剪public_state，避免过大日志：仅输出当前玩家与回合序号
                        ps_summary = {
                            "current_player": int(self._current_turn_player.value) if self._current_turn_player else None,
                            "turn": len(self._game_logic.history.records) + 1 if (self._game_logic and self._game_logic.history) else None,
                        }
                        biz_logger.info({
                            "event": "ai_action",
                            "player_id": player_id,
                            "seat_faction": self._seat_to_faction.get(player_id),
                            "public_state_summary": ps_summary,
                            "action": action,
                        })
                    except Exception:
                        pass
                    # 新增：缓存本席位的模型utterance，等待回合结束后再广播与TTS
                    try:
                        self._cache_utterance(player_id, action, public_state)
                    except Exception:
                        pass
                    # 输出交给上层消费（执行走子/渲染/记录）
                    if self._ai_action_consumer:
                        try:
                            self._ai_action_consumer(action)
                        except Exception as ce:
                            try:
                                self._logger.warning(f"[DEBUG] AI输出消费失败：{ce}")
                            except Exception:
                                pass
                    else:
                        try:
                            # 控制台仅输出摘要，避免完整提示词出现在debug
                            utter = str(action.get("utterance") or "")
                            self._logger.info(
                                f"[DEBUG] AI选择动作已生成（摘要）：move={action.get('move')} utterance_len={len(utter)}"
                            )
                        except Exception:
                            pass
                        return
                except Exception as me:
                    try:
                        self._logger.warning(f"[DEBUG] 模型选择动作失败：{me}")
                    except Exception:
                        pass
                    return
            # 删除外部调度器兜底逻辑
            return
        except Exception as e:
            try:
                self._logger.warning(f"[DEBUG] 调度AI失败：{e}")
            except Exception:
                pass

    def on_game_started(self, first_player: Player) -> None:
        """① 游戏开始信号：首手玩家已产生，触发一次AI调度（若当前为AI）。"""
        # 记录当前回合席位与方位，供调度与后续判断使用
        self._current_turn_player = first_player
        try:
            self._current_turn_faction = self._seat_to_faction.get(int(first_player.value))
        except Exception:
            self._current_turn_faction = None
        # 重置最近LLM调用时间，确保首次等待3秒
        self._last_llm_call_ts = None
        # 改为后台线程触发，避免阻塞UI
        self._start_background_dispatch()

    def on_turn_changed(self, current_player: Player) -> None:
        """② 回合变化信号：先处理上一席位的待广播utterance（含TTS），再触发AI调度。"""
        # 保存上一席位（刚结束回合的玩家）
        prev_player = self._current_turn_player
        # 更新当前回合席位与方位
        self._current_turn_player = current_player
        try:
            self._current_turn_faction = self._seat_to_faction.get(int(current_player.value))
        except Exception:
            self._current_turn_faction = None
        # 主动通知窗口层进行一次UI刷新，确保通过API执行的动作能即时显示
        if self._broadcast_consumer:
            try:
                self._broadcast_consumer({"event": "ui_refresh"})
            except Exception:
                pass
        # 改为后台线程处理待广播并调度AI，避免阻塞UI
        self._start_background_dispatch(prev_player)

    def on_player_eliminated(self, player: Player) -> None:
        """③ 玩家死亡（淘汰）信号：不直接调度AI，仅记录日志。"""
        try:
            self._logger.info(f"[DEBUG] 玩家淘汰：seat={player}")
        except Exception:
            pass

    def on_game_finished(self) -> None:
        """④ 游戏结束信号：不再调度AI。"""
        try:
            self._logger.info("[DEBUG] 对局结束，停止进程层AI调度。")
        except Exception:
            pass

    def _cache_utterance(self, player_id: int, action: Dict[str, Any], public_state: Dict[str, Any]) -> None:
        """将模型返回的 utterance 暂存，等待该席位回合结束（on_turn_changed）时触发广播与TTS。"""
        if not isinstance(action, dict):
            return
        text = str(action.get("utterance") or "")
        if not text:
            # 无内容不缓存
            return
        # 统一全场广播，不再从模型输出解析定向参数
        target = "all"
        faction = self._seat_to_faction.get(player_id) or "south"
        self._pending_utterances[player_id] = {
            "text": text,
            "target": target,
            "seat": player_id,
            "speaker_faction": faction,
        }

    def _start_background_dispatch(self, prev_player: Optional[Player] = None) -> None:
        """在后台线程处理待广播与AI调度，避免阻塞Qt事件循环。"""
        if self._ai_worker_busy:
            try:
                self._logger.debug("[DEBUG] AI后台调度线程忙，排队一次后续调度。")
            except Exception:
                pass
            # 记录一次待执行调度（仅保留最近一次）
            self._pending_dispatch = True
            self._pending_prev_player = prev_player
            return
        def _run():
            self._ai_worker_busy = True
            try:
                # 先处理上一席位的待广播
                if prev_player is not None:
                    try:
                        self._handle_pending_broadcast(prev_player)
                    except Exception as ex:
                        try:
                            self._logger.warning(f"[DEBUG] 后台处理待广播失败：{ex}")
                        except Exception:
                            pass
                # 再调度AI（含最低响应时间控制与模型调用）
                self._schedule_ai_safe()
            finally:
                self._ai_worker_busy = False
                # 若期间有新的调度请求被排队，立即启动下一次调度
                if self._pending_dispatch:
                    next_prev = self._pending_prev_player
                    # 清空排队标记，避免重复
                    self._pending_dispatch = False
                    self._pending_prev_player = None
                    try:
                        self._logger.debug("[DEBUG] 触发排队的后续AI调度。")
                    except Exception:
                        pass
                    # 再次启动后台调度（上一席位按最后一次排队的 prev_player 传入）
                    self._start_background_dispatch(next_prev)
        try:
            t = threading.Thread(target=_run, daemon=True)
            self._ai_worker_thread = t
            t.start()
        except Exception as e:
            try:
                self._logger.warning(f"[DEBUG] 启动AI后台调度线程失败：{e}")
            except Exception:
                pass

    def _handle_pending_broadcast(self, prev_player: Optional[Player]) -> None:
        """若上一席位（刚结束回合）存在待广播的utterance：
        1) 读取该席位的TTS凭据（若未配置则仅文本广播）；
        2) 调用 DoubaoTTSClient 合成音频到临时文件；
        3) 将 {text, seat, speaker_faction, target, audio_path?} 交给UI层消费。"""
        if prev_player is None:
            return
        try:
            prev_id = int(prev_player.value)
        except Exception:
            return
        payload = self._pending_utterances.pop(prev_id, None)
        if not payload or not payload.get("text"):
            return
        audio_path: Optional[str] = None
        # 新增：按“人格编号（player1-3）”读取TTS凭据，避免将 seat 与 player 混用
        persona = self.get_persona_for_seat(prev_player)
        persona_suffix = None
        try:
            if persona and isinstance(persona, str) and persona.startswith("player"):
                _n = int(persona[6:])
                if _n in (1, 2, 3):
                    persona_suffix = f"P{_n}"
        except Exception:
            persona_suffix = None
        # 读取人格专属或通用TTS凭据（不再使用 seat 编号）
        appid = (os.environ.get(f"DOUBAO_TTS_APPID_{persona_suffix}") if persona_suffix else None) or os.environ.get("DOUBAO_TTS_APPID")
        access_token = (os.environ.get(f"DOUBAO_TTS_ACCESS_TOKEN_{persona_suffix}") if persona_suffix else None) or os.environ.get("DOUBAO_TTS_ACCESS_TOKEN")
        secret_key = (os.environ.get(f"DOUBAO_TTS_SECRET_KEY_{persona_suffix}") if persona_suffix else None) or os.environ.get("DOUBAO_TTS_SECRET_KEY")
        cluster = (os.environ.get(f"DOUBAO_TTS_CLUSTER_{persona_suffix}") if persona_suffix else None) or os.environ.get("DOUBAO_TTS_CLUSTER")
        voice_type = (os.environ.get(f"DOUBAO_TTS_VOICE_TYPE_{persona_suffix}") if persona_suffix else None) or os.environ.get("DOUBAO_TTS_VOICE_TYPE")
        try:
            creds_ok = bool(appid and access_token and secret_key and cluster and voice_type)
            try:
                self._logger.info(f"[TTS] 凭据可用={creds_ok}（不输出具体值，确保安全）")
            except Exception:
                pass
        except Exception:
            pass
        # 若凭据完整，则尝试合成
        if appid and access_token and secret_key and cluster and voice_type:
            try:
                client = DoubaoTTSClient(
                    appid=appid,
                    access_token=access_token,
                    secret_key=secret_key,
                    cluster=cluster,
                    voice_type=voice_type,
                    encoding="wav",
                )
                # 生成临时文件路径（改为wav以提升Windows默认解码兼容性）
                tmp_fd, tmp_path = tempfile.mkstemp(prefix=f"junqi_tts_{prev_id}_", suffix=".wav")
                os.close(tmp_fd)
                audio_path = client.synthesize_to_file(payload["text"], tmp_path)
                try:
                    self._logger.debug(f"[DEBUG] TTS合成成功，音频文件：{audio_path}")
                except Exception:
                    pass
                # 兼容性增强：若返回的实际音频头非WAV，则尝试识别为MP3并改名，避免播放器因扩展名不匹配而失败
                try:
                    with open(audio_path, "rb") as af:
                        head = af.read(12)
                    is_wav = len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"
                    is_mp3 = len(head) >= 3 and (head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0))
                    if not is_wav and is_mp3:
                        # 改为 .mp3 扩展名，交由 Qt 媒体播放器播放（fallback 仅支持 .wav，不影响）
                        new_path = audio_path[:-4] + ".mp3" if audio_path.lower().endswith(".wav") else audio_path + ".mp3"
                        try:
                            os.replace(audio_path, new_path)
                            audio_path = new_path
                            try:
                                self._logger.debug(f"[DEBUG] 检测为MP3流，已改名：{audio_path}")
                            except Exception:
                                pass
                        except Exception:
                            # 改名失败不影响原路径，但可能导致播放失败；记录一次告警
                            try:
                                self._logger.warning("[WARN] TTS音频为MP3但扩展名为WAV，改名失败，可能无法播放")
                            except Exception:
                                pass
                except Exception:
                    # 读取头失败不影响后续流程
                    pass
            except Exception:
                audio_path = None
        # 交给UI层消费（展示文本并播放音频）
        try:
            if self._broadcast_consumer:
                data = dict(payload)
                data["player_id"] = prev_id
                if audio_path:
                    data["audio_path"] = audio_path
                self._broadcast_consumer(data)
                try:
                    self._logger.debug("[DEBUG] 已广播聊天到UI层（含音频路径与文本）")
                except Exception:
                    pass
        except Exception:
            pass
        # 新增：历史完整日志（走子与聊天）改为写入 biz.log（junqi_ai.biz）
        try:
            # 准备 biz 日志器与文件处理器
            biz_logger = logging.getLogger("junqi_ai.biz")
            biz_logger.setLevel(logging.INFO)
            biz_logger.propagate = False
            logs_dir = os.path.join(os.getcwd(), "logs")
            os.makedirs(logs_dir, exist_ok=True)
            biz_log_path = os.path.join(logs_dir, "biz.log")
            need_handler = True
            for h in biz_logger.handlers:
                if isinstance(h, logging.FileHandler):
                    try:
                        if getattr(h, "baseFilename", None) == biz_log_path:
                            need_handler = False
                            break
                    except Exception:
                        continue
            if need_handler:
                fh = logging.FileHandler(biz_log_path, encoding="utf-8")
                fh.setLevel(logging.INFO)
                fh.setFormatter(logging.Formatter('%(asctime)s\t%(message)s'))
                biz_logger.addHandler(fh)
            # 写入历史快照为JSON字符串
            history_payload = {
                "event": "history_snapshot",
                "turn": len(self._game_logic.history.records) if (self._game_logic and self._game_logic.history) else 0,
                "records": self._game_logic.history.to_list() if (self._game_logic and self._game_logic.history) else [],
                "chats": self._game_logic.history.to_chat_list() if (self._game_logic and self._game_logic.history) else [],
            }
            biz_logger.info(json.dumps(history_payload, ensure_ascii=False))
        except Exception:
            pass
    # [removed] 误插入的窗口层广播处理函数已移除