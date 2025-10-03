import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .doubao_client import DoubaoClient


class JunqiAgent:
    """
    四国军棋AI代理：构建提示词、调用Doubao模型并解析JSON动作。
    - 输入：公开的棋盘状态（不包含敏感信息）、候选合法走法列表（可包含评分/标签）、AI玩家ID
    - 输出：选择的合法走法（from, to）与可选的理由/置信度
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "doubao-seed-1.6-250615") -> None:
        self.client = DoubaoClient(api_key=api_key, model=model)

    def _build_messages(self, public_state: Dict[str, Any], legal_moves: List[Dict[str, Any]], player_id: int) -> List[Dict[str, str]]:
        """构建系统与用户提示。
        重点：严格要求仅从提供的合法走法中选择其一，并以JSON输出。
        候选可能附带以下字段以辅助决策：score（越高越好）、risk_level（low/medium/high）、reward_level、tactics（战术标签）、reason（简述）。
        """
        # 动态注入玩家主题权重（优先使用 persona_assignments 解耦合人物与席位）
        from .prompt_themes import get_theme_weights, get_theme_weights_by_persona, render_theme_prompt
        persona_assignments = public_state.get("persona_assignments")
        theme_weights = None
        if isinstance(persona_assignments, dict):
            # 兼容字符串/整数键
            persona_key = persona_assignments.get(player_id) or persona_assignments.get(str(player_id))
            if isinstance(persona_key, str) and persona_key:
                theme_weights = get_theme_weights_by_persona(persona_key)
        if theme_weights is None:
            # 回退到按玩家编号的兼容映射（不与方位/颜色绑定，仅作为占位）
            theme_weights = get_theme_weights(player_id)
        theme_block = render_theme_prompt(theme_weights)
        system = (
            "你是四国军棋（Junqi）AI助手。你的任务是在当前回合为指定玩家仅选择一个合法走法。\n"
            "角色设定：你具备‘贴吧老哥’的说话风格——喜欢吐槽、阴阳怪气、玩梗，必要时也会骂人，但所有发言必须紧扣当前棋局与公开信息。严禁无关话题与泄露隐藏身份。\n"
            "称呼规范：四方以颜色别名称呼：南=小红，北=小绿，西=淡淡色，东=橙猫猫。若对某一方定向喊话，请在输出中提供 utterance_target（字符串），值为上述称呼之一；未提供则默认为对全场喊话。不得捏造或使用未在提供的 faction_names 中的称呼。\n"
            + theme_block +
            "决策偏好（若候选提供评分与标签）：\n"
            "- 综合考虑候选的 score（更高更佳）与 risk_level（偏好 low），在得分接近时可参考 reward_level 与 tactics。\n"
            "- 常见 tactics 标签：attack_win（吃子且大概率存活）、attack_trade（可能互换）、rail_sprint（铁路快攻/调度）、central_control（控制中心）、defend_flag（保卫军旗）、camp_hold（行营驻守）、scout（侦察）、reposition（位形优化）。\n"
            "- 默认采用稳健均衡风格：兼顾吃子收益、风险控制、中心控制与防守需求。\n"
            "规则要点（由服务器保证合法性，你无需再验证规则细节）：\n"
            "- 不可从大本营出发；地雷与军旗不可移动。\n"
            "- 普通格仅允许四方向邻格移动或战斗。\n"
            "- 铁路上可直线远距离移动，工程师可转弯；路径不可越过阻挡。\n"
            "- 战斗由服务器判定结果。\n"
            "历史使用准则：\n"
            "- 你可以参考最近若干回合的对局历史（history），其中包含阵营、起止本地坐标与结果，用于判断节奏、侦察与风险，但不可断言任何尚未公开的敌方棋子真实身份。\n"
            "- 允许在 rationale 中基于历史进行概率性推断（如‘疑似低级子’），但必须避免‘上帝视角’与绝对断言。\n"
            "输出要求（必须遵守，无任何例外）：\n"
            "- 仅输出一个用代码块包裹的 JSON 对象：以 ```json 开始，纯 JSON 内容，以 ``` 结束；代码块外不得出现任何文字、标点或空行。\n"
            "- JSON 键和值必须严格使用如下架构：{\"move\": {\"from\": {\"row\": 整数, \"col\": 整数}, \"to\": {\"row\": 整数, \"col\": 整数}}, \"selected_id\": 整数, \"rationale\": 字符串, \"confidence\": 数值, \"utterance\": 字符串, \"utterance_target\": 可选字符串}。\n"
            "- move 坐标必须与从 legal_moves 中选择的候选的 from/to 完全一致，selected_id 必须是该候选的 id；不得捏造或猜测不存在的 id。\n"
            "- utterance 为对全场说的一句短评（最多15个中文字符），必须与当前局面/最近走法紧密相关；可以体现‘贴吧老哥’风格。\n"
            "- 只能从提供的 legal_moves 列表中选择一个作为 move；禁止输出数组、解释文字、示例、前后缀、JSON 外围文字、Markdown 标题、额外字段或尾随逗号。\n"
            "- 若无法确定最优解，也必须选择一个合法走法并给出合理的 rationale 与保守的 confidence（0–1 之间）。\n"
        )
        user_payload = {
            "game_state": public_state.get("state"),
            "current_player": public_state.get("current_player"),
            "current_player_faction": public_state.get("current_player_faction"),
            "faction_names": public_state.get("faction_names"),
            "teams": public_state.get("teams"),
            "acting_player_id": player_id,
            "board": public_state.get("board", {}),
            "history": public_state.get("history", []),
            "legal_moves": legal_moves,
        }
        user = (
            "这是当前公开的棋盘状态、对局历史（history：包含 turn/player_faction/from_local/to_local/outcome/death_count）与该玩家的合法走法候选（可能包含 score/risk_level/reward_level/tactics/reason）。请从 legal_moves 中挑选一个最佳走法并返回JSON。\n" +
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

    def choose_action(self, public_state: Dict[str, Any], legal_moves: List[Dict[str, Any]], player_id: int) -> Dict[str, Any]:
        """
        调用模型选择动作，并返回解析后的字典：
        {
            "move": {"from": {"row": int, "col": int}, "to": {"row": int, "col": int}},
            "selected_id": int,
            "rationale": str,
            "confidence": float,
            "utterance": str,
            "utterance_target": Optional[str]  # 可选：点名某一方，须为提供的颜色称呼之一
        }
        若解析失败或选择非法，将抛出异常，由上层决定回退策略。
        """
        messages = self._build_messages(public_state, legal_moves, player_id)
        output_text = self.client.chat(messages)
        data = self._extract_json(output_text)
        # 基本结构校验
        if "move" not in data or "from" not in data["move"] or "to" not in data["move"]:
            raise ValueError("Invalid action JSON: missing move/from/to")
        # 优先使用 selected_id；若缺失则根据 move 坐标在候选中推断，无法推断则不阻断流程
        f = data["move"]["from"]
        t = data["move"]["to"]
        sid = data.get("selected_id")
        match = None
        if isinstance(sid, int):
            match = next((m for m in legal_moves if m.get("id") == sid), None)
            if match:
                mf = match["from"]
                mt = match["to"]
                if not (mf["row"] == f["row"] and mf["col"] == f["col"] and mt["row"] == t["row"] and mt["col"] == t["col"]):
                    # 若 selected_id 与坐标不一致，尝试改为按坐标匹配候选的 id
                    match = next(
                        (m for m in legal_moves
                         if isinstance(m, dict)
                         and isinstance(m.get("from"), dict)
                         and isinstance(m.get("to"), dict)
                         and int(m["from"]["row"]) == int(f["row"]) 
                         and int(m["from"]["col"]) == int(f["col"]) 
                         and int(m["to"]["row"]) == int(t["row"]) 
                         and int(m["to"]["col"]) == int(t["col"]))
                        , None)
                    if match and isinstance(match.get("id"), int):
                        data["selected_id"] = int(match["id"])  # 用坐标匹配的结果覆盖 selected_id
                    else:
                        # 找不到匹配，则移除 selected_id，放宽处理（由上层合法性校验）
                        data.pop("selected_id", None)
                # 若一致则保留原 selected_id
            else:
                # 现有 sid 不在候选中：尝试坐标匹配
                match = next(
                    (m for m in legal_moves
                     if isinstance(m, dict)
                     and isinstance(m.get("from"), dict)
                     and isinstance(m.get("to"), dict)
                     and int(m["from"]["row"]) == int(f["row"]) 
                     and int(m["from"]["col"]) == int(f["col"]) 
                     and int(m["to"]["row"]) == int(t["row"]) 
                     and int(m["to"]["col"]) == int(t["col"]))
                    , None)
                if match and isinstance(match.get("id"), int):
                    data["selected_id"] = int(match["id"])  # 用坐标匹配推断 sid
                else:
                    data.pop("selected_id", None)
        else:
            # sid 缺失或类型不正确：尝试通过坐标匹配候选，推断 selected_id
            match = next(
                (m for m in legal_moves
                 if isinstance(m, dict)
                 and isinstance(m.get("from"), dict)
                 and isinstance(m.get("to"), dict)
                 and int(m["from"]["row"]) == int(f["row"]) 
                 and int(m["from"]["col"]) == int(f["col"]) 
                 and int(m["to"]["row"]) == int(t["row"]) 
                 and int(m["to"]["col"]) == int(t["col"]))
                , None)
            if match and isinstance(match.get("id"), int):
                data["selected_id"] = int(match["id"])  # 成功推断则补齐 sid
        # ---- 新增：字段类型与范围校验 ----
        # rationale
        if "rationale" in data and not isinstance(data["rationale"], str):
            raise ValueError("rationale 必须为字符串")
        # confidence
        if "confidence" in data:
            conf = data["confidence"]
            if not (isinstance(conf, int) or isinstance(conf, float)):
                raise ValueError("confidence 必须为数值")
            # 约束到 [0,1]
            if conf < 0 or conf > 1:
                # 自动裁剪到范围内，避免报错阻断流程
                data["confidence"] = max(0.0, min(1.0, float(conf)))
        else:
            # 若缺失，则给一个保守默认值
            data["confidence"] = 0.5
        # utterance 清洗与长度限制（最多15中文字符）
        ut = data.get("utterance")
        if ut is None:
            # 给一个短默认，避免TTS失败
            data["utterance"] = "稳一点先"
        elif not isinstance(ut, str):
            raise ValueError("utterance 必须为字符串")
        else:
            # 简单清洗：去除中英文括号内舞台说明
            ut_clean = re.sub(r"[\(（][^\)）]*[\)）]", "", ut)
            ut_clean = ut_clean.strip()
            # 长度裁剪到15字符（Python按代码点裁剪即可）
            if len(ut_clean) > 15:
                ut_clean = ut_clean[:15]
            data["utterance"] = ut_clean if ut_clean else "稳一点先"
        # utterance_target 校验：必须在 faction_names 值集合内
        target = data.get("utterance_target")
        if target is not None:
            if not isinstance(target, str):
                # 非法类型则移除
                data.pop("utterance_target", None)
            else:
                names = public_state.get("faction_names") or {}
                valid_values = set(names.values()) if isinstance(names, dict) else set()
                if target not in valid_values:
                    # 非法值则移除，避免前端或TTS端使用
                    data.pop("utterance_target", None)
        return data

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