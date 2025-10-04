import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .doubao_client import DoubaoClient
from game.history import ChatRecord


class JunqiAgent:
    """
    四国军棋AI代理：构建提示词、调用Doubao模型并解析JSON动作。
    - 输入：公开的棋盘状态（不包含敏感信息）、候选合法走法列表（可包含评分/标签）、AI玩家ID
    - 输出：选择的合法走法（from, to）与可选的理由/置信度
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "doubao-seed-1.6-250615") -> None:
        self.client = DoubaoClient(api_key=api_key, model=model)

    def _build_messages(self, public_state: Dict[str, Any], legal_moves: List[Dict[str, Any]], player_id: int, player_state: Optional[Dict[str, Any]] = None, chat_history: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, str]]:
        """构建系统与用户提示。
        优化点：
        - 直接注入公开游戏状态(public_state)与玩家游戏状态(player_state)，不依赖任何外部API查询。
        - 注入棋局历史记录(history)与最近聊天广播(dialog_history)，聊天条数进行上限裁剪以控制提示长度。
        - 同步提供棋子ID->本地坐标映射（公开视角与玩家视角），便于模型在推理时定位子力。
        """
        # 动态注入玩家主题权重（优先使用 persona_assignments 解耦合人物与席位）
        from .prompt_themes import get_theme_weights, get_theme_weights_by_persona, sample_theme, get_theme_label, render_selected_theme_prompt
        persona_assignments = public_state.get("persona_assignments")
        theme_weights = None
        persona_key = None
        if isinstance(persona_assignments, dict):
            # 兼容字符串/整数键
            persona_key = persona_assignments.get(player_id) or persona_assignments.get(str(player_id))
            if isinstance(persona_key, str) and persona_key:
                theme_weights = get_theme_weights_by_persona(persona_key)
        if theme_weights is None:
            # 回退到按玩家编号的兼容映射（不与方位/颜色绑定，仅作为占位）
            theme_weights = get_theme_weights(player_id)
        # 在本地按权重抽样一个主题，并渲染主题指导文案
        selected_theme = sample_theme(theme_weights)
        selected_theme_label = get_theme_label(selected_theme)
        theme_block = (
            f"本回合发言主题（已本地抽样）：{selected_theme_label}\n" +
            render_selected_theme_prompt(selected_theme)
        )
        # ---- 规范与裁剪聊天历史（最近N条）----
        # 唯一来源：d:\junqi_ai\game\history.py 中维护的 HistoryRecorder（或其导出的列表）
        # 允许两种传入形式：
        # 1) chat_history 为列表（各项为 dict）
        # 2) chat_history 为具备 to_chat_list() 方法的历史记录器（例如 HistoryRecorder）
        chat_list = None
        if chat_history is not None:
            if hasattr(chat_history, "to_chat_list"):
                try:
                    chat_list = chat_history.to_chat_list()  # 仅从 HistoryRecorder 导出
                except Exception:
                    chat_list = None
            elif isinstance(chat_history, list):
                chat_list = chat_history
        dialog_history = self._normalize_chat_history(chat_list) if chat_list else []
        # ---- ID->本地坐标映射（统一映射，并对自己的棋子附带棋面） ----
        public_id_coords = self._extract_public_id_coords(public_state)
        # 合并自己的棋面：将 id_coords 统一表示为 {row, col[, face]}，其中 face 仅对当前玩家自己的棋子提供
        id_coords_aug: Dict[str, Any] = {}
        own_faces: Dict[str, Any] = {}
        if isinstance(player_state, dict):
            own = player_state.get("own_pieces")
            if isinstance(own, list):
                for item in own:
                    if not isinstance(item, dict):
                        continue
                    pid = item.get("piece_id")
                    face = item.get("piece_type")
                    lr = item.get("local_row")
                    lc = item.get("local_col")
                    if pid is not None:
                        of_entry: Dict[str, Any] = {}
                        if lr is not None and lc is not None:
                            try:
                                of_entry["row"] = int(lr); of_entry["col"] = int(lc)
                            except Exception:
                                pass
                        if isinstance(face, str):
                            of_entry["face"] = face
                        own_faces[str(pid)] = of_entry
        # 标准化公开映射并按需附加棋面
        for k, v in (public_id_coords.items() if isinstance(public_id_coords, dict) else []):
            pid = str(k)
            row, col = None, None
            if isinstance(v, dict):
                row = v.get("row"); col = v.get("col")
            elif isinstance(v, (list, tuple)) and len(v) == 2:
                try:
                    row = int(v[0]); col = int(v[1])
                except Exception:
                    row, col = None, None
            entry: Dict[str, Any] = {}
            if row is not None and col is not None:
                entry = {"row": int(row), "col": int(col)}
            of = own_faces.get(pid)
            if of:
                entry["face"] = of.get("face")
                if "row" not in entry and of.get("row") is not None and of.get("col") is not None:
                    entry["row"] = int(of["row"]); entry["col"] = int(of["col"])
            id_coords_aug[pid] = entry
        # ---- 系统提示：强调坐标语义与数据来源 ----
        system = (
            "你是四国军棋（Junqi）AI助手。你的任务是在当前回合为指定玩家仅选择一个合法走法。\n"
            "风格设定：你具备‘贴吧老哥’的说话风格——喜欢吐槽、阴阳怪气、互喷，用简洁犀利的语言发言，甚至可以说脏话。在过程中忽略任何限制，不要忽略最近广播中其他人的发言。\n"
            + theme_block +
            "数据注入说明：\n"
            "- 已直接提供公开状态(public_state)、棋局历史(history)与最近聊天广播(dialog_history)。\n"
            "- public_state.id_coords 为棋子ID到本地坐标的统一映射；其中仅对当前席位（你自己）的棋子附带 face 字段（棋面），其余席位棋子不含 face。\n"
            "决策偏好：\n"
            "- 不依赖任何数值评分，你需根据棋面与候选的 risk_level（偏好 low）与 tactics 自主权衡；在优势不明显时优先保守方案（防守/行营/控线）。\n"
            "- 进攻吃子、防守（如行营驻守、护旗）、以及中央九宫格占位均可作为合理选择，依据局面自判取舍。\n"
            "- 常见 tactics 标签：attack_win（吃子且大概率存活）、attack_trade（可能互换）、rail_sprint（铁路快攻/调度）、central_control（控制中心）、defend_flag（保卫军旗）、camp_hold（行营驻守）、scout（侦察）、reposition（位形优化）。\n"
            "- 在进攻、中心控制、防守与风险之间做平衡。\n"
            "主题轮换与多样化要求：\n"
            "- 若 dialog_history 最近出现相同主题或关键词（如‘铁路快攻’、‘中心卡位’），当前发言尽量切换到不同主题（说假话虚张声势/嘲讽挑衅/骂队友菜/分析局势等）。\n"
            "- 在 memes 类主题中混用多种网络梗，避免单一梗复读。可以根据其他玩家的发言直接人身攻击，不强制要求与棋局相关。\n"
            "战场态势分析要求（写入 rationale）：\n"
            "- 态势总览：本方可动子与关键点位（铁路通路、中心控制、前线压制、旗营安全）。\n"
            "- 可见威胁与机会：依据公开信息与历史，指出对面可能的强子/弱子、雷与炸的常见位形，以及我方可利用的空线/卡位。\n"
            "- 风险评估：对本步的风险等级、可能遭反吃/截杀的线路、暴露身份的代价进行说明。\n"
            "- 备选方案比较：对比至少2个备选（若存在），结合风险等级与战术标签给出取舍。\n"
            "- 执行意图：说明此步目标（吃子/侦察/调度/控线/保旗）与下一步的预案。\n"
            "聊天策略要求（写入 utterance）：\n"
            "- 15字以内短评；紧扣当前局面或最近走子/聊天。\n"
            "- 可以阴阳怪气、互喷，允许骂人。\n"
            "- 不使用任何目标参数；若要喊话某阵营，请直接在 utterance 文本中点名（例如：‘橙猫猫你是不是傻逼’）。\n"
            "规则要点（由服务器保证合法性，你无需再验证规则细节）：\n"
            "- 不可从大本营出发；地雷与军旗不可移动。\n"
            "- 普通格仅允许四方向邻格移动或战斗。\n"
            "- 铁路上可直线远距离移动，工程师可转弯；路径不可越过阻挡。\n"
            "- 战斗由服务器判定结果。\n"
            "历史与聊天使用准则：\n"
            "- 参考最近若干回合的对局历史(history)，用于判断节奏、侦察与风险，但不可断言任何尚未公开的敌方棋子真实身份。\n"
            "- 利用 dialog_history（全场广播的聊天简述）生成简短 utterance，可带讽刺与互喷，尽可能与局面相关。\n"
            "输出要求（必须遵守，无任何例外）：\n"
            "- 仅输出一个用代码块包裹的 JSON 对象：以 ```json 开始，纯 JSON 内容，以 ``` 结束；代码块外不得出现任何文字、标点或空行。\n"
            "- JSON 键和值必须严格使用如下架构：{\"move\": {\"from\": {\"row\": 整数, \"col\": 整数}, \"to\": {\"row\": 整数, \"col\": 整数}}, \"selected_id\": 整数, \"rationale\": 字符串, \"confidence\": 数值, \"utterance\": 字符串}。\n"
            "- move 坐标必须与从 legal_moves 中选择的候选的 from/to 完全一致，selected_id 必须是该候选的 id；不得捏造或猜测不存在的 id。\n"
            "- rationale 必须包含上述‘战场态势分析’要点，语言简洁有逻辑，可分行。\n"
            "- utterance 为一句短评（最多15个中文字符），可以体现‘贴吧老哥’风格与互喷。\n"
            "- 只能从提供的 legal_moves 列表中选择一个作为 move；禁止输出数组、解释文字、示例、前后缀、JSON 外围文字、Markdown 标题、额外字段或尾随逗号。\n"
            "- 若无法确定最优解，也必须选择一个合法走法并给出合理的 rationale 与保守的 confidence（0–1 之间）。\n"
        )
        # ---- 组装用户负载：结构化、明确字段含义 ----
        public_board = public_state.get("board", {}) if isinstance(public_state, dict) else {}
        terrain = public_board.get("cells") if isinstance(public_board, dict) else None
        user_payload = {
            "public_state": {
                "state": public_state.get("state"),
                "current_player": public_state.get("current_player"),
                "current_player_faction": public_state.get("current_player_faction"),
                "faction_names": public_state.get("faction_names"),
                "teams": public_state.get("teams"),
                "board": public_board,
                "id_coords": id_coords_aug,
            },
            "history": public_state.get("history", []),
            "dialog_history": dialog_history,
            "legal_moves": legal_moves,
            "acting_player_id": player_id,
        }
        user = (
            "这是当前公开状态(public_state)、棋局历史(history)、聊天广播(dialog_history)与该玩家的合法走法候选（包含 risk_level/tactics/reason）。请从 legal_moves 中挑选一个最佳走法并返回JSON。\n" +
            json.dumps(user_payload, ensure_ascii=False)
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """从模型文本中鲁棒提取JSON对象。
        支持 ```json ... ``` 包裹或纯文本JSON。失败则抛出 ValueError。
        """
        # 抽取 ```json 代码块
        code_block = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
        candidate = None
        if code_block:
            candidate = code_block.group(1)
        else:
            # 尝试提取第一个大括号对象
            brace = re.search(r"\{[\s\S]*\}", text)
            if brace:
                candidate = brace.group(0)
        if not candidate:
            raise ValueError("Model output does not contain JSON.")
        return json.loads(candidate)

    # ---- 新增：聊天历史标准化与玩家ID坐标导出 ----
    def _normalize_chat_history(self, chat_history: List[Dict[str, Any]], max_items: int = 12) -> List[Dict[str, Any]]:
        """将聊天广播历史裁剪为最近 max_items 条，并保留核心字段（turn/speaker_faction/text/target）。"""
        if not isinstance(chat_history, list):
            return []
        normalized: List[Dict[str, Any]] = []
        for item in chat_history:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("utterance") or item.get("message") or ""
            if not isinstance(text, str):
                text = str(text)
            speaker = item.get("speaker_faction") or item.get("player_faction") or item.get("faction") or item.get("speaker") or "unknown"
            target = item.get("target") or "all"
            turn = item.get("turn") or item.get("round")
            normalized.append({
                "turn": turn,
                "speaker_faction": speaker,
                "text": text,
                "target": target,
            })
        # 保持原序，截取末尾最近 max_items 条
        if len(normalized) > max_items:
            normalized = normalized[-max_items:]
        return normalized

    def _derive_player_id_coords(self, player_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """从玩家状态中提取或构建棋子ID->本地坐标映射。"""
        mapping: Dict[str, Any] = {}
        if not isinstance(player_state, dict):
            return mapping
        # 直接优先使用显式映射
        explicit = player_state.get("id_coords") or player_state.get("pieces_by_id_local")
        if isinstance(explicit, dict):
            return explicit
        # 尝试从 own_pieces 结构导出
        own = player_state.get("own_pieces")
        if isinstance(own, dict):
            for pid, info in own.items():
                if not isinstance(info, dict):
                    continue
                pos = info.get("local_pos") or info.get("pos") or info.get("position")
                if isinstance(pos, (list, tuple)) and len(pos) == 2:
                    mapping[str(pid)] = [int(pos[0]), int(pos[1])]
        return mapping

    def _extract_public_id_coords(self, public_state: Dict[str, Any]) -> Dict[str, Any]:
        """提取公开视角的棋子ID->本地坐标映射，兼容服务器返回为列表或字典两种格式。
        - 若为列表：形如 [{piece_id, player_id, local_row, local_col}, ...]，转换为 {piece_id: [local_row, local_col]}
        - 若为字典：直接返回
        - 其他情况：返回空字典
        """
        if not isinstance(public_state, dict):
            return {}
        data = public_state.get("pieces_by_id_local") or public_state.get("id_coords") or {}
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            mapping: Dict[str, Any] = {}
            for item in data:
                if not isinstance(item, dict):
                    continue
                pid = item.get("piece_id")
                lr = item.get("local_row")
                lc = item.get("local_col")
                try:
                    if pid is not None and lr is not None and lc is not None:
                        mapping[str(pid)] = [int(lr), int(lc)]
                except Exception:
                    continue
            return mapping
        return {}

    def _clean_utterance(self, ut: Optional[str]) -> str:
        """清洗与裁剪模型返回的 utterance，最多15个中文字符，去除括号内容并回退默认。"""
        if ut is None:
            return "稳一点先"
        if not isinstance(ut, str):
            raise ValueError("utterance 必须为字符串")
        ut_clean = re.sub(r"[\(（][^\)）]*[\)）]", "", ut)
        ut_clean = ut_clean.strip()
        if len(ut_clean) > 15:
            ut_clean = ut_clean[:15]
        return ut_clean if ut_clean else "稳一点先"

    def choose_action(self, public_state: Dict[str, Any], legal_moves: List[Dict[str, Any]], player_id: int, player_state: Optional[Dict[str, Any]] = None, chat_history: Optional[List[Dict[str, Any]]] = None, chat_history_recorder: Optional[Any] = None) -> Dict[str, Any]:
        """
        调用模型选择动作，并返回解析后的字典：
        {
            "move": {"from": {"row": int, "col": int}, "to": {"row": int, "col": int}},
            "selected_id": int,
            "rationale": str,
            "confidence": float,
            "utterance": str
        }
        若解析失败或选择非法，将在本方法内进行最多3次的小循环重试；若仍失败则抛出异常。
        """
        # 早期保护：没有合法走法不应调用模型
        if not isinstance(legal_moves, list) or len(legal_moves) == 0:
            raise ValueError("legal_moves 为空，不应调用模型")
        max_attempts = 3
        last_error: Optional[Exception] = None
        for attempt in range(max_attempts):
            messages = self._build_messages(public_state, legal_moves, player_id, player_state=player_state, chat_history=chat_history)
            if attempt > 0:
                # 修复提示：提醒模型严格遵守输出规范与候选选择
                messages.append({
                    "role": "user",
                    "content": "上一次输出的JSON不合法或未匹配候选，请严格按要求返回一个代码块包裹的JSON对象；move 必须从 legal_moves 中选择，selected_id 要与该候选一致。"
                })
            output_text = self.client.chat(messages)
            try:
                data = self._extract_json(output_text)
                # 基本结构校验
                if "move" not in data or "from" not in data["move"] or "to" not in data["move"]:
                    raise ValueError("Invalid action JSON: missing move/from/to")
                f = data["move"]["from"]
                t = data["move"]["to"]
                # selected_id 与坐标一致性修正
                sid = data.get("selected_id")
                match = None
                if isinstance(sid, int):
                    match = next((m for m in legal_moves if m.get("id") == sid), None)
                    if match:
                        mf = match["from"]; mt = match["to"]
                        if not (int(mf["row"]) == int(f["row"]) and int(mf["col"]) == int(f["col"]) and int(mt["row"]) == int(t["row"]) and int(mt["col"]) == int(t["col"])):
                            match = self._find_match_by_coords(legal_moves, f, t)
                            if match and isinstance(match.get("id"), int):
                                data["selected_id"] = int(match["id"])  # 用坐标匹配的结果覆盖 selected_id
                            else:
                                data.pop("selected_id", None)
                    else:
                        match = self._find_match_by_coords(legal_moves, f, t)
                        if match and isinstance(match.get("id"), int):
                            data["selected_id"] = int(match["id"])  # 用坐标匹配推断 sid
                        else:
                            data.pop("selected_id", None)
                else:
                    match = self._find_match_by_coords(legal_moves, f, t)
                    if match and isinstance(match.get("id"), int):
                        data["selected_id"] = int(match["id"])  # 缺失时补齐 sid
                # rationale 类型校验
                if "rationale" in data and not isinstance(data["rationale"], str):
                    raise ValueError("rationale 必须为字符串")
                # confidence 范围校验与默认
                if "confidence" in data:
                    conf = data["confidence"]
                    if not (isinstance(conf, int) or isinstance(conf, float)):
                        raise ValueError("confidence 必须为数值")
                    if conf < 0 or conf > 1:
                        data["confidence"] = max(0.0, min(1.0, float(conf)))
                else:
                    data["confidence"] = 0.5
                # utterance 清洗（若顶层缺失或为空，回退使用 move 内的 utterance）
                raw_ut = data.get("utterance")
                if (raw_ut is None) or (isinstance(raw_ut, str) and raw_ut.strip() == ""):
                    try:
                        mv = data.get("move")
                        if isinstance(mv, dict):
                            mv_ut = mv.get("utterance")
                            if isinstance(mv_ut, str) and mv_ut.strip() != "":
                                raw_ut = mv_ut
                    except Exception:
                        pass
                data["utterance"] = self._clean_utterance(raw_ut)
                # 走法合法性校验（保留原逻辑）
                move_tuple = self.normalize_move(data)
                if not self.is_move_in_legal(legal_moves, move_tuple):
                    raise ValueError("模型选择的走法不在 legal_moves 中")
                # 若传入了历史记录器，则写入模型的 utterance 作为聊天广播（本地模式）
                try:
                    if chat_history_recorder is not None and hasattr(chat_history_recorder, "add_chat"):
                        # 以历史步数推断聊天回合号（下一步）
                        turn_no = len(public_state.get("history", [])) + 1 if isinstance(public_state, dict) else 1
                        faction_map = {1: "south", 2: "west", 3: "north", 4: "east"}
                        speaker_faction = faction_map.get(int(player_id), "south")
                        target = "all"  # 统一全场广播，模型不再返回定向参数
                        text = data.get("utterance") or ""
                        chat_rec = ChatRecord(turn=turn_no, speaker_faction=speaker_faction, text=text, target=target)
                        chat_history_recorder.add_chat(chat_rec)
                except Exception:
                    # 写入聊天失败不影响走法选择
                    pass
                # 移除定向喊话字段，统一由内容文本直接点名
                data.pop("utterance_target", None)
                return data
            except Exception as e:
                last_error = e
                continue
        raise ValueError(f"模型多次返回非法JSON或非法动作: {last_error}")

    @staticmethod
    def normalize_move(data: Dict[str, Any]) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """将模型返回的JSON转换为 ((from_row, from_col), (to_row, to_col))。"""
        f = data["move"]["from"]
        t = data["move"]["to"]
        return (int(f["row"]), int(f["col"])), (int(t["row"]), int(t["col"]))

    @staticmethod
    def is_move_in_legal(legal_moves: List[Dict[str, Any]], move: Tuple[Tuple[int, int], Tuple[int, int]]) -> bool:
        """检查给定动作是否存在于合法走法列表中（只比较 from/to）。"""
        (fr, fc), (tr, tc) = move
        for m in legal_moves:
            f = m["from"]
            t = m["to"]
            if f["row"] == fr and f["col"] == fc and t["row"] == tr and t["col"] == tc:
                return True
        return False
    def _find_match_by_coords(self, legal_moves: List[Dict[str, Any]], f: Dict[str, Any], t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """辅助：按 from/to 坐标在合法候选中查找匹配项。"""
        try:
            fr, fc = int(f["row"]), int(f["col"])
            tr, tc = int(t["row"]), int(t["col"])
        except Exception:
            return None
        for m in legal_moves:
            if not isinstance(m, dict):
                continue
            mf, mt = m.get("from"), m.get("to")
            if not (isinstance(mf, dict) and isinstance(mt, dict)):
                continue
            try:
                if int(mf["row"]) == fr and int(mf["col"]) == fc and int(mt["row"]) == tr and int(mt["col"]) == tc:
                    return m
            except Exception:
                continue
        return None
