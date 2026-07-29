"""Valida que RC_ROOM_* resolve para o grupo privado SNAS e que o admin pode convidar."""
from src.config import load_settings
from src.rocketchat import RocketChatClient

s = load_settings()
rc = RocketChatClient(s.rc_base_url, s.rc_admin_user, s.rc_admin_password)
rc.login()
print("login OK")
print("RC_ROOM_ID=", s.rc_room_id)
print("RC_ROOM_NAME=", s.rc_room_name)

room = rc.resolve_default_room(s.rc_room_id, s.rc_room_name)
rid = room["_id"]
print("resolve name=", room.get("name"), "id=", rid, "t=", room.get("t"))

assert (room.get("name") or "").upper() == "SNAS"
assert rid == s.rc_room_id
assert room.get("t") == "p", "SNAS deve ser grupo privado (t=p)"

rc.invite_to_channel(rid, rc.user_id, room_type="p")
print("admin pode convidar no SNAS: OK")
print("VALIDACAO OK: canal padrao = SNAS (privado), joinDefaultChannels=false")
