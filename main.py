"""Servidor de salas do Tela4K (FastAPI + WebSocket), estilo Discord.

Quem conecta recebe a lista de salas (fixas e criadas) e clica para entrar. Dentro da sala todos sao iguais:
cada um pode transmitir e assistir quando quiser. O servidor so guarda quem esta onde e repassa as mensagens
iniciais da conexao de video (WebRTC). O VIDEO NAO PASSA POR AQUI: vai direto entre os PCs.

Rodar localmente:  uvicorn main:app --host 0.0.0.0 --port 8000
Variaveis opcionais: TELA4K_SALAS_FIXAS="Jogos,Valorant,Filmes", TELA4K_MAX_ROOMS, TELA4K_MAX_MEMBERS, TELA4K_MAX_CONN
"""
import asyncio
import json
import os

from fastapi import FastAPI, WebSocket

from rooms import RoomManager, RoomError, new_member_id

_fixed = os.getenv("TELA4K_SALAS_FIXAS")
app = FastAPI(title="Tela4K Salas")
manager = RoomManager(fixed=[s for s in _fixed.split(",")] if _fixed else None,
                      max_rooms=int(os.getenv("TELA4K_MAX_ROOMS", "50")),
                      max_members=int(os.getenv("TELA4K_MAX_MEMBERS", "10")))
MAX_CONN = int(os.getenv("TELA4K_MAX_CONN", "100"))
MAX_SIGNAL_BYTES = 32768
MAX_FRAME = 65536
sockets = {}  # id do membro -> WebSocket


@app.get("/health")
def health():
    return {"ok": True, "rooms": manager.room_count(), "online": len(sockets)}


async def send(ws, msg):
    try:
        await ws.send_json(msg)
    except Exception:
        pass


async def broadcast(member_ids, msg):
    for mid in list(member_ids):
        ws = sockets.get(mid)
        if ws is not None:
            await send(ws, msg)


async def push_rooms():
    """Lista de salas atualizada para todos os conectados (como a barra lateral do Discord)."""
    await broadcast(list(sockets), {"type": "rooms", "rooms": manager.rooms_payload()})


async def push_members(room_id):
    room = manager.rooms.get(room_id)
    if room is not None:
        await broadcast(list(room.members), {"type": "members", "room": room.id,
                                              "members": manager.members_payload(room)})


async def after_move(room, left):
    """Avisa quem ficou na sala antiga, quem esta na nova e a lista geral."""
    if left is not None and not left.closed:
        await push_members(left.room_id)
    if room is not None:
        await push_members(room.id)
    await push_rooms()


def parse(text):
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def joined_msg(room, mid):
    return {"type": "joined", "me": mid, "room": {"id": room.id, "name": room.name, "fixed": room.fixed},
            "members": manager.members_payload(room)}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    if len(sockets) >= MAX_CONN:
        await send(ws, {"type": "error", "code": "busy", "message": "Servidor lotado. Tente de novo em instantes."})
        await ws.close()
        return
    mid = new_member_id()
    manager.connect(mid)
    sockets[mid] = ws
    try:
        await send(ws, {"type": "welcome", "me": mid, "rooms": manager.rooms_payload()})
        await push_rooms()
        while True:
            text = await ws.receive_text()
            if len(text) > MAX_FRAME:
                break
            msg = parse(text)
            t = msg.get("type")
            try:
                if t == "ping":
                    await send(ws, {"type": "pong"})
                elif t == "hello":
                    manager.rename(mid, msg.get("name"))
                elif t == "join":
                    manager.rename(mid, msg.get("name"))
                    room, left = manager.join_room(mid, msg.get("room"))
                    await send(ws, joined_msg(room, mid))
                    await after_move(room, left)
                elif t == "create":
                    manager.rename(mid, msg.get("name"))
                    room, left = manager.create_room(mid, msg.get("room"))
                    await send(ws, joined_msg(room, mid))
                    await after_move(room, left)
                elif t == "leave":
                    left = manager.leave_room(mid)
                    await send(ws, {"type": "left"})
                    await after_move(None, left)
                elif t == "stream":
                    room_id = manager.set_streaming(mid, msg.get("on"))
                    await push_members(room_id)
                    await push_rooms()
                elif t == "signal":
                    # sinalizacao WebRTC (oferta/resposta): so entre membros da mesma sala
                    to, data = msg.get("to"), msg.get("data")
                    if isinstance(to, str) and isinstance(data, dict) and manager.same_room(mid, to) \
                            and len(json.dumps(data)) <= MAX_SIGNAL_BYTES:
                        target = sockets.get(to)
                        if target is not None:
                            await send(target, {"type": "signal", "from": mid, "data": data})
            except RoomError as e:
                await send(ws, {"type": "error", "code": e.code, "message": e.message})
    except Exception:
        # desconexao normal ou frame invalido: tudo cai na limpeza abaixo
        pass
    finally:
        sockets.pop(mid, None)
        left = manager.disconnect(mid)
        if left is not None and not left.closed:
            await push_members(left.room_id)
        await push_rooms()
