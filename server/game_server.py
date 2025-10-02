#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四国军棋游戏WebSocket服务器
"""

import asyncio
import json
import websockets
from typing import Dict, Set, Optional, Any
from websockets.server import WebSocketServerProtocol
import logging

from ..game.game_logic import GameLogic
from ..game.piece import Player
from ..game.board import Position

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
        
    async def register_client(self, websocket: WebSocketServerProtocol) -> Optional[Player]:
        """注册新客户端"""
        # 分配玩家ID
        available_players = set(Player) - self.connected_players
        if not available_players:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "游戏房间已满"
            }))
            return None
        
        player = min(available_players, key=lambda p: p.value)
        self.clients[websocket] = player
        self.connected_players.add(player)
        
        logger.info(f"玩家 {player.name} 已连接")
        
        # 发送欢迎消息
        await websocket.send(json.dumps({
            "type": "welcome",
            "player_id": player.value,
            "message": f"欢迎，您是玩家 {player.value}"
        }))
        
        # 广播玩家加入消息
        await self.broadcast({
            "type": "player_joined",
            "player_id": player.value,
            "connected_players": [p.value for p in self.connected_players]
        }, exclude=websocket)
        
        return player
    
    async def unregister_client(self, websocket: WebSocketServerProtocol):
        """注销客户端"""
        if websocket in self.clients:
            player = self.clients[websocket]
            del self.clients[websocket]
            self.connected_players.discard(player)
            
            logger.info(f"玩家 {player.name} 已断开连接")
            
            # 广播玩家离开消息
            await self.broadcast({
                "type": "player_left",
                "player_id": player.value,
                "connected_players": [p.value for p in self.connected_players]
            })
    
    async def handle_message(self, websocket: WebSocketServerProtocol, message: str):
        """处理客户端消息"""
        try:
            data = json.loads(message)
            message_type = data.get("type")
            player = self.clients.get(websocket)
            
            if not player:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": "未注册的客户端"
                }))
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
            elif message_type == "set_mark":
                await self.handle_set_mark(websocket, data, player)
            else:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": f"未知的消息类型: {message_type}"
                }))
                
        except json.JSONDecodeError:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "无效的JSON格式"
            }))
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"服务器错误: {str(e)}"
            }))
    
    async def handle_move_piece(self, websocket: WebSocketServerProtocol, data: Dict, player: Player):
        """处理移动棋子请求"""
        try:
            from_pos = Position(data["from_row"], data["from_col"])
            to_pos = Position(data["to_row"], data["to_col"])
            
            success = self.game_logic.move_piece(from_pos, to_pos)
            
            response = {
                "type": "move_result",
                "success": success,
                "from_pos": {"row": from_pos.row, "col": from_pos.col},
                "to_pos": {"row": to_pos.row, "col": to_pos.col}
            }
            
            if success:
                # 广播移动结果给所有玩家
                await self.broadcast({
                    "type": "piece_moved",
                    "player_id": player.value,
                    "from_pos": {"row": from_pos.row, "col": from_pos.col},
                    "to_pos": {"row": to_pos.row, "col": to_pos.col},
                    "game_state": self.get_public_game_state()
                })
            else:
                await websocket.send(json.dumps(response))
                
        except KeyError as e:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"缺少必要参数: {e}"
            }))
    
    async def handle_place_piece(self, websocket: WebSocketServerProtocol, data: Dict, player: Player):
        """处理放置棋子请求"""
        try:
            position = Position(data["row"], data["col"])
            piece_type = data["piece_type"]
            
            # 这里需要根据piece_type创建相应的棋子
            # 简化实现，实际需要更完善的棋子创建逻辑
            
            response = {
                "type": "place_result",
                "success": False,  # 暂时返回失败，需要完善实现
                "message": "放置棋子功能待完善"
            }
            
            await websocket.send(json.dumps(response))
            
        except KeyError as e:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"缺少必要参数: {e}"
            }))
    
    async def handle_auto_layout(self, websocket: WebSocketServerProtocol, player: Player):
        """处理自动布局请求"""
        self.game_logic.auto_layout_player(player)
        
        await self.broadcast({
            "type": "auto_layout_complete",
            "player_id": player.value,
            "game_state": self.get_public_game_state()
        })
    
    async def handle_start_game(self, websocket: WebSocketServerProtocol):
        """处理开始游戏请求"""
        success = self.game_logic.start_game()
        
        await self.broadcast({
            "type": "game_started" if success else "start_game_failed",
            "success": success,
            "message": "游戏开始！" if success else "无法开始游戏，请检查所有玩家是否完成布局",
            "game_state": self.get_public_game_state()
        })
    
    async def handle_reset_game(self, websocket: WebSocketServerProtocol):
        """处理重置游戏请求"""
        self.game_logic.reset_game()
        
        await self.broadcast({
            "type": "game_reset",
            "message": "游戏已重置",
            "game_state": self.get_public_game_state()
        })
    
    async def handle_get_game_state(self, websocket: WebSocketServerProtocol):
        """处理获取游戏状态请求"""
        await websocket.send(json.dumps({
            "type": "game_state",
            "state": self.get_public_game_state()
        }))
    
    async def handle_set_mark(self, websocket: WebSocketServerProtocol, data: Dict, player: Player):
        """处理设置标记请求"""
        try:
            position = Position(data["row"], data["col"])
            mark = data["mark"]
            
            self.game_logic.board.set_player_mark(position, mark)
            
            await websocket.send(json.dumps({
                "type": "mark_set",
                "success": True,
                "position": {"row": position.row, "col": position.col},
                "mark": mark
            }))
            
        except KeyError as e:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"缺少必要参数: {e}"
            }))
    
    def get_public_game_state(self) -> Dict[str, Any]:
        """获取公开的游戏状态（不包含敏感信息）"""
        state = self.game_logic.get_game_state()
        
        # 添加棋盘状态（只显示可见的棋子）
        board_state = {}
        for position, cell in self.game_logic.board.cells.items():
            pos_key = f"{position.row},{position.col}"
            cell_info = {
                "type": cell.cell_type.name,
                "player_area": cell.player_area.value if cell.player_area else None
            }
            
            if cell.piece:
                # 只显示棋子的基本信息，不显示具体类型（四暗模式）
                cell_info["has_piece"] = True
                cell_info["piece_player"] = cell.piece.player.value
            else:
                cell_info["has_piece"] = False
            
            board_state[pos_key] = cell_info
        
        state["board"] = board_state
        state["connected_players"] = [p.value for p in self.connected_players]
        
        return state
    
    async def broadcast(self, message: Dict, exclude: Optional[WebSocketServerProtocol] = None):
        """广播消息给所有客户端"""
        if not self.clients:
            return
        
        message_str = json.dumps(message)
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
            await asyncio.Future()  # 永远运行

def main():
    """服务器主函数"""
    server = GameServer()
    asyncio.run(server.start_server())

if __name__ == "__main__":
    main()