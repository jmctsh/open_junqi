from typing import Dict, Optional, List, Callable
import logging
from game.piece import Player
from typing import Any
# 新增：进程层直接接入规则层与AI层
from game.game_logic import GameLogic
from ai.agent import JunqiAgent
from server.strategies.scoring import score_legal_moves, choose_best_move_styled
from server.perspectives.manager import PerspectiveManager
# 新增：TTS客户端与临时文件支持
import os
import tempfile
from ai.tts_client import DoubaoTTSClient
# 新增：后台线程与时间控制
import threading
import time
import random
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
        # 默认（全局）代理：当未配置按人格代理时使用
        self._agent: Optional[JunqiAgent] = None
        # 新增：按人格的代理映射（persona -> agent），例如 player1/player2/player3
        self._persona_agents: Dict[str, JunqiAgent] = {}
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
        # 新增：视角管理器（提供位置线索注入）
        self._perspective_mgr: PerspectiveManager = PerspectiveManager()

    def set_game_logic(self, gl: GameLogic) -> None:
        """注册规则层 GameLogic，进程层可据此构建 public_state 与合法走法。"""
        self._game_logic = gl
        # 附加到视角管理器并立即刷新
        try:
            self._perspective_mgr.attach_game_logic(gl)
        except Exception:
            pass

    def attach_agent(self, agent: JunqiAgent) -> None:
        """注册 AI 代理（JunqiAgent）。"""
        self._agent = agent

    def attach_persona_agents(self, agents: Dict[str, JunqiAgent]) -> None:
        """注册按人格的 AI 代理映射：键为 "player1"/"player2"/"player3"。
        若某人格未提供代理，则回退使用全局代理（若存在）。"""
        if not isinstance(agents, dict):
            return
        # 仅保留允许的人格键
        for k, v in list(agents.items()):
            if k in self._allowed_personas and isinstance(v, JunqiAgent):
                self._persona_agents[k] = v
        try:
            keys = ",".join(sorted(self._persona_agents.keys())) or "(none)"
            self._logger.info(f"[DEBUG] 已注册人格代理：{keys}")
        except Exception:
            pass

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

    def _get_best_move(self, player: Player) -> Optional[Dict[str, Any]]:
        gl = self._game_logic
        if gl is None:
            return None
        raw = gl.board.enumerate_player_legal_moves(player)
        try:
            self._logger.debug(f"[DEBUG] 计算最佳走法：seat={player} 合法候选数={len(raw)}")
        except Exception:
            pass
        try:
            # 优先采用带风格与“反击”判定的选择器；异常时返回 None 交由上层处理
            return choose_best_move_styled(gl.board, player, raw, gl.history)
        except Exception:
            return None

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
            # 优先使用内置的 JunqiAgent 调度链路（若已注册依赖）；否则走“无模型回退”
            # 新增：按人格选择具体代理
            persona = self.get_current_turn_persona()
            agent = None
            if persona and persona in self._persona_agents:
                agent = self._persona_agents.get(persona)
            else:
                agent = self._agent
            if agent and self._game_logic:
                try:
                    self._logger.debug(
                        f"[DEBUG] 触发AI调度：seat={self._current_turn_player}, faction={self._current_turn_faction}, persona={persona}"
                    )
                except Exception:
                    pass
                # 刷新并构建当前席位视角的“位置线索”负载
                try:
                    self._perspective_mgr.refresh(self._game_logic)
                except Exception:
                    pass
                location_payload = self._perspective_mgr.build_location_clues_payload(self._current_turn_player)
                best_move = self._get_best_move(self._current_turn_player) if self._current_turn_player else None
                # 无候选：深度搜索失败或无合法走法，执行跳过回合
                if not best_move:
                    try:
                        self._logger.debug("[DEBUG] 无最佳走法（深度搜索未产出或无合法走法），执行跳过回合。")
                    except Exception:
                        pass
                    try:
                        if self._game_logic:
                            self._game_logic.skip_turn()
                    except Exception:
                        pass
                    return
                player_id = int(self._current_turn_player.value)
                # 统一数据源：仅注入位置线索
                try:
                    action = agent.choose_action(
                        location_payload,
                        player_id,
                        best_move,
                        chat_history=self._game_logic.history if self._game_logic else None,
                        chat_history_recorder=self._game_logic.history if self._game_logic else None,
                    )
                    # 记录本次调用时间戳
                    try:
                        import time
                        self._last_llm_call_ts = time.time()
                    except Exception:
                        pass
                    # 文件业务日志已移除：保留控制台摘要输出，避免生成 d:\junqi_ai\logs 下的文件
                    # 新增：缓存本席位的模型utterance，等待回合结束后再广播与TTS
                    try:
                        self._cache_utterance(player_id, action)
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
            else:
                # === 无模型回退：本地算法直接执行最佳走法，并使用兜底发言 ===
                if not self._game_logic:
                    return
                try:
                    self._perspective_mgr.refresh(self._game_logic)
                except Exception:
                    pass
                planned = self._get_best_move(self._current_turn_player) if self._current_turn_player else None
                if not planned:
                    try:
                        self._logger.debug("[DEBUG] 无模型回退：无最佳走法（深度搜索未产出或无合法走法），执行跳过回合。")
                    except Exception:
                        pass
                    try:
                        if self._game_logic:
                            self._game_logic.skip_turn()
                    except Exception:
                        pass
                    return
                try:
                    mf = planned.get("from") or {}
                    mt = planned.get("to") or {}
                    action = {
                        "move": {
                            "from": {"row": int(mf.get("row")), "col": int(mf.get("col"))},
                            "to": {"row": int(mt.get("row")), "col": int(mt.get("col"))},
                        },
                        # 统一字段，与模型输出保持一致
                        "selected_id": planned.get("id") if isinstance(planned.get("id"), int) else None,
                        "rationale": "本地算法直接执行最佳走法。",
                        "confidence": 0.6,
                        # 复用兜底发言内容（与 agent._clean_utterance 的默认一致）
                        "utterance": "稳一点先",
                    }
                    # 写入聊天历史（保持与模型路径一致的效果）
                    try:
                        from game.history import ChatRecord
                        turn_no = len(getattr(self._game_logic.history, "records", [])) + 1
                        faction_map = {1: "south", 2: "west", 3: "north", 4: "east"}
                        player_id = int(self._current_turn_player.value) if self._current_turn_player else 1
                        speaker_faction = faction_map.get(player_id, "south")
                        chat_rec = ChatRecord(turn=turn_no, speaker_faction=speaker_faction, text=action["utterance"], target="all")
                        self._game_logic.history.add_chat(chat_rec)
                    except Exception:
                        pass
                    # 缓存待广播 utterance，并执行走子
                    try:
                        pid = int(self._current_turn_player.value) if self._current_turn_player else None
                        if pid is not None:
                            self._cache_utterance(pid, action)
                    except Exception:
                        pass
                    if self._ai_action_consumer:
                        try:
                            self._ai_action_consumer(action)
                        except Exception as ce:
                            try:
                                self._logger.warning(f"[DEBUG] 无模型回退：AI输出消费失败：{ce}")
                            except Exception:
                                pass
                    else:
                        try:
                            utter = str(action.get("utterance") or "")
                            self._logger.info(
                                f"[DEBUG] 无模型回退：move={action.get('move')} utterance_len={len(utter)}"
                            )
                        except Exception:
                            pass
                    # 文件业务日志已移除：仅保留控制台输出
                except Exception as e:
                    try:
                        self._logger.warning(f"[DEBUG] 无模型回退失败：{e}")
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

    def _cache_utterance(self, player_id: int, action: Dict[str, Any]) -> None:
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
                # 取消AI间的固定随机等待，直接调度以让深度搜索占用时间预算
                try:
                    pass
                except Exception:
                    pass
                # 再调度AI（模型调用）
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
        # 历史文件日志已移除：不再写入 d:\junqi_ai\logs\biz.log
    # [removed] 误插入的窗口层广播处理函数已移除