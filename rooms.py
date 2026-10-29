"""Logica das salas do Tela4K (sem dependencias externas, para ser testada sozinha).

Etapa 1: criar sala, gerar codigo de convite, entrar, listar participantes, fechar a sala
quando o anfitriao sai. Nenhum video passa por aqui.
"""
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field

# Sem 0/O e 1/I/L, para o codigo nao ser confundido ao ser lido ou digitado.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 6


class RoomError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Member:
    id: str
    name: str
    role: str  # "host" ou "viewer"


@dataclass
class Room:
    code: str
    host_id: str
    members: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class Left:
    room_code: str
    was_host: bool
    others: list  # ids que continuam conectados a essa sala (no caso do anfitriao: serao desconectados)


def clean_name(name, default):
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(name or ""))
    name = re.sub(r"\s+", " ", name).strip()[:24]
    return name or default


def normalize_code(code):
    return "".join(c for c in str(code or "").upper() if c in ALPHABET)


def new_member_id():
    return uuid.uuid4().hex[:12]


class RoomManager:
    def __init__(self, max_rooms=200, max_members=10):
        self.max_rooms = max_rooms
        self.max_members = max_members
        self.rooms = {}       # codigo -> Room
        self.member_room = {}  # id do membro -> codigo

    def room_count(self):
        return len(self.rooms)

    def _new_code(self):
        for _ in range(50):
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
            if code not in self.rooms:
                return code
        raise RoomError("server_busy", "Não consegui gerar um código agora. Tente de novo.")

    def create(self, name, member_id):
        if member_id in self.member_room:
            raise RoomError("already_in_room", "Você já está em uma sala.")
        if len(self.rooms) >= self.max_rooms:
            raise RoomError("server_full", "O servidor atingiu o limite de salas. Tente mais tarde.")
        code = self._new_code()
        room = Room(code=code, host_id=member_id)
        room.members[member_id] = Member(member_id, clean_name(name, "Anfitrião"), "host")
        self.rooms[code] = room
        self.member_room[member_id] = code
        return room

    def join(self, code, name, member_id):
        if member_id in self.member_room:
            raise RoomError("already_in_room", "Você já está em uma sala.")
        norm = normalize_code(code)
        room = self.rooms.get(norm) if len(norm) == CODE_LEN else None
        if room is None:
            raise RoomError("not_found", "Sala não encontrada. Confira o código.")
        if len(room.members) >= self.max_members:
            raise RoomError("room_full", f"A sala está cheia (máximo {self.max_members} pessoas).")
        room.members[member_id] = Member(member_id, clean_name(name, "Convidado"), "viewer")
        self.member_room[member_id] = norm
        return room

    def leave(self, member_id):
        code = self.member_room.pop(member_id, None)
        room = self.rooms.get(code) if code else None
        if room is None:
            return None
        member = room.members.pop(member_id, None)
        if member is None:
            return None
        if member.role == "host":
            others = list(room.members)
            for mid in others:
                self.member_room.pop(mid, None)
            del self.rooms[code]
            return Left(code, True, others)
        return Left(code, False, list(room.members))

    def same_room(self, a, b):
        """True se os dois membros estao na mesma sala (so assim o servidor repassa sinalizacao entre eles)."""
        ca = self.member_room.get(a)
        return ca is not None and a != b and ca == self.member_room.get(b)

    def members_payload(self, room):
        return [{"id": m.id, "name": m.name, "role": m.role} for m in room.members.values()]

    def snapshot(self, room, member_id):
        return {"code": room.code, "you": member_id,
                "role": room.members[member_id].role,
                "members": self.members_payload(room)}
