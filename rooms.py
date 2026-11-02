"""Logica das salas do Tela4K, estilo Discord (sem dependencias externas, para ser testada sozinha).

- Existem salas FIXAS (sempre na lista, mesmo vazias) e salas CRIADAS pelos usuarios (somem quando ficam vazias).
- Quem se conecta ve a lista de salas e clica para entrar: nao ha codigo de convite.
- Dentro da sala todos sao iguais: qualquer um pode transmitir (varios ao mesmo tempo) e assistir quando quiser.
Nenhum video passa por aqui; so quem esta em qual sala e quem esta transmitindo.
"""
import re
import unicodedata
import uuid
from dataclasses import dataclass, field

DEFAULT_FIXED = ["Jogos", "Valorant", "Filmes e séries", "Bate-papo"]
MAX_MEMBER_NAME = 24
MAX_ROOM_NAME = 28


class RoomError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def new_member_id():
    return uuid.uuid4().hex[:12]


def clean_name(name, default="Convidado", limit=MAX_MEMBER_NAME):
    """Tira caracteres de controle e espacos repetidos; nome vazio vira o padrao."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(name or ""))
    text = re.sub(r"\s+", " ", text).strip()[:limit].strip()
    return text or default


def room_key(name):
    """Identificador da sala: sem acento, minusculo, espacos normalizados ("Filmes e séries" -> "filmes e series")."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass
class Member:
    id: str
    name: str
    room: str = None          # id da sala em que esta (None = so olhando a lista)
    streaming: bool = False   # esta transmitindo a tela agora?


@dataclass
class Room:
    id: str
    name: str
    fixed: bool = False
    members: dict = field(default_factory=dict)   # id -> Member, na ordem de entrada


@dataclass
class Left:
    room_id: str
    closed: bool   # True: era uma sala criada e ficou vazia, entao foi apagada


class RoomManager:
    def __init__(self, fixed=None, max_members=10, max_rooms=50):
        self.max_members, self.max_rooms = max_members, max_rooms
        self.rooms = {}      # id -> Room (fixas primeiro, depois as criadas)
        self.members = {}    # id -> Member (todos os conectados)
        for name in (DEFAULT_FIXED if fixed is None else fixed):
            name = clean_name(name, default="", limit=MAX_ROOM_NAME)
            if name and room_key(name) not in self.rooms:
                self.rooms[room_key(name)] = Room(room_key(name), name, fixed=True)

    # ---------------------------------------------------------------- conexao
    def connect(self, member_id, name=""):
        member = Member(member_id, clean_name(name))
        self.members[member_id] = member
        return member

    def disconnect(self, member_id):
        """Remove o membro de tudo. Devolve Left (sala que ele deixou) ou None."""
        left = self._leave(member_id)
        self.members.pop(member_id, None)
        return left

    def rename(self, member_id, name):
        member = self.members.get(member_id)
        if member is not None and str(name or "").strip():
            member.name = clean_name(name)

    # ---------------------------------------------------------------- salas
    def _leave(self, member_id):
        member = self.members.get(member_id)
        if member is None or member.room is None:
            return None
        room = self.rooms.get(member.room)
        member.room, member.streaming = None, False
        if room is None:
            return None
        room.members.pop(member_id, None)
        if not room.fixed and not room.members:
            del self.rooms[room.id]
            return Left(room.id, True)
        return Left(room.id, False)

    def leave_room(self, member_id):
        if member_id not in self.members:
            raise RoomError("unknown", "Conexão desconhecida.")
        return self._leave(member_id)

    def _enter(self, member, room):
        room.members[member.id] = member
        member.room = room.id

    def join_room(self, member_id, room_id):
        """Entra numa sala existente (saindo da anterior). Devolve (sala, Left da sala anterior ou None)."""
        member = self.members.get(member_id)
        if member is None:
            raise RoomError("unknown", "Conexão desconhecida.")
        room = self.rooms.get(room_key(room_id))
        if room is None:
            raise RoomError("not_found", "Essa sala não existe mais.")
        if member.room == room.id:
            return room, None
        if len(room.members) >= self.max_members:   # confere ANTES de sair da sala atual
            raise RoomError("room_full", f"A sala \"{room.name}\" está cheia ({self.max_members} pessoas).")
        left = self._leave(member_id)
        self._enter(member, room)
        return room, left

    def create_room(self, member_id, room_name):
        """Cria uma sala nova e entra nela. Devolve (sala, Left da sala anterior ou None)."""
        member = self.members.get(member_id)
        if member is None:
            raise RoomError("unknown", "Conexão desconhecida.")
        name = clean_name(room_name, default="", limit=MAX_ROOM_NAME)
        if not name:
            raise RoomError("bad_name", "Digite um nome para a sala.")
        key = room_key(name)
        if key in self.rooms:
            raise RoomError("exists", f"Já existe uma sala chamada \"{self.rooms[key].name}\".")
        if sum(1 for r in self.rooms.values() if not r.fixed) >= self.max_rooms:
            raise RoomError("too_many", "Há salas demais abertas agora. Entre em uma existente.")
        left = self._leave(member_id)
        room = Room(key, name, fixed=False)
        self.rooms[key] = room
        self._enter(member, room)
        return room, left

    def set_streaming(self, member_id, on):
        member = self.members.get(member_id)
        if member is None or member.room is None:
            raise RoomError("not_in_room", "Entre em uma sala para transmitir.")
        member.streaming = bool(on)
        return member.room

    def same_room(self, a, b):
        """True se os dois estao na mesma sala (so assim o servidor repassa sinalizacao entre eles)."""
        ma, mb = self.members.get(a), self.members.get(b)
        return bool(ma and mb and a != b and ma.room is not None and ma.room == mb.room)

    # ---------------------------------------------------------------- resumos para enviar aos clientes
    def room_count(self):
        return len(self.rooms)

    def members_payload(self, room):
        return [{"id": m.id, "name": m.name, "streaming": m.streaming} for m in room.members.values()]

    def rooms_payload(self):
        return [{"id": r.id, "name": r.name, "fixed": r.fixed, "count": len(r.members),
                 "streaming": sum(1 for m in r.members.values() if m.streaming),
                 "members": [m.name for m in list(r.members.values())[:6]]} for r in self.rooms.values()]
