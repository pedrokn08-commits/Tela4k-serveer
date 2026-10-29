"""Servidor de salas do Tela4K (FastAPI + WebSocket). Salas, convites, participantes e sinalizacao WebRTC (so mensagens; o video vai direto entre os PCs).

Rodar localmente:  uvicorn main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import os

from fastapi import FastAPI, WebSocket

from rooms import RoomManager, RoomError, new_member_id

app = FastAPI(title="Tela4K Salas")
manager = RoomManager(max_rooms=int(os.getenv("TELA4K_MAX_ROOMS", "200")),
                      max_members=int(os.getenv("TELA4K_MAX_MEMBERS", "10")))
sockets = {}  # id do membro -> WebSocket
MAX_SIGNAL_BYTES = 32768


@app.get("/health")
def health():
    return {"ok": True, "rooms": manager.room_count()}


async def send(ws, msg):
    try:
        await ws.send_json(msg)
    except Exception:
        pass


async def broadcast(member_ids, msg):
    for mid in member_ids:
        ws = sockets.get(mid)
        if ws is not None:
            await send(ws, msg)


def parse(text):
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    mid = None
    try:
        first = parse(await asyncio.wait_for(ws.receive_text(), timeout=15))
        kind = first.get("type")
        member_id = new_member_id()
        try:
            if kind == "create":
                room = manager.create(first.get("name"), member_id)
            elif kind == "join":
                room = manager.join(first.get("code"), first.get("name"), member_id)
            else:
                raise RoomError("bad_request", "Mensagem inicial inválida.")
        except RoomError as e:
            await send(ws, {"type": "error", "code": e.code, "message": e.message})
            await ws.close()
            return
        mid = member_id
        sockets[mid] = ws
        await send(ws, {"type": "created" if kind == "create" else "joined", **manager.snapshot(room, mid)})
        await broadcast(list(room.members), {"type": "members", "members": manager.members_payload(room)})

        while True:
            msg = parse(await ws.receive_text())
            t = msg.get("type")
            if t == "ping":
                await send(ws, {"type": "pong"})
            elif t == "signal":
                # sinalizacao WebRTC (oferta/resposta): so repassa entre membros da mesma sala; o video NAO passa por aqui
                to, data = msg.get("to"), msg.get("data")
                if isinstance(to, str) and isinstance(data, dict) and manager.same_room(mid, to) \
                        and len(json.dumps(data)) <= MAX_SIGNAL_BYTES:
                    target = sockets.get(to)
                    if target is not None:
                        await send(target, {"type": "signal", "from": mid, "data": data})
    except Exception:
        # desconexao normal, timeout da primeira mensagem ou frame invalido: tudo cai na limpeza abaixo
        pass
    finally:
        if mid is not None:
            sockets.pop(mid, None)
            left = manager.leave(mid)
            if left is not None:
                if left.was_host:
                    await broadcast(left.others, {"type": "room_closed", "reason": "O anfitrião saiu da sala."})
                    for oid in left.others:
                        other = sockets.pop(oid, None)
                        if other is not None:
                            try:
                                await other.close()
                            except Exception:
                                pass
                else:
                    room = manager.rooms.get(left.room_code)
                    if room is not None:
                        await broadcast(left.others, {"type": "members", "members": manager.members_payload(room)})
