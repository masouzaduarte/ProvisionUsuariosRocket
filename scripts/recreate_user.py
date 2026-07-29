import time
from src.config import load_settings
from src.rocketchat import RocketChatClient
from src.checkpoint import Checkpoint
from src.password import gerar_senha

settings = load_settings()
rc = RocketChatClient(settings.rc_base_url, settings.rc_admin_user, settings.rc_admin_password, timeout=180)
rc.login()

username = "marco.memora"
email = "marco.duarte@memora.com.br"
nome = "Marco Antonio de Souza Duarte"

user = rc.find_by_username(username)
print("by username:", user.get("_id") if user else None, user.get("username") if user else None)
user_email = rc.find_by_email(email)
print("by email:", user_email.get("_id") if user_email else None, user_email.get("username") if user_email else None)

target = user or user_email
if target:
    uid = target["_id"]
    print(f"Deletando {uid}...")
    try:
        rc.delete_user(uid)
        print("delete OK")
    except Exception as exc:
        print("delete exception:", type(exc).__name__, exc)

    for i in range(6):
        time.sleep(2)
        ainda = rc.find_by_username(username)
        print(f"check {i+1}: existe={bool(ainda)}")
        if not ainda:
            break
    else:
        # tenta de novo
        ainda = rc.find_by_username(username)
        if ainda:
            print("Tentando delete novamente...")
            try:
                rc.delete_user(ainda["_id"])
            except Exception as exc:
                print("2a delete exception:", exc)
            time.sleep(3)

ainda = rc.find_by_username(username) or rc.find_by_email(email)
if ainda:
    print("FALHA: usuario ainda existe, abortando recreate. id=", ainda.get("_id"))
    raise SystemExit(2)

print("Usuario ausente. Recriando...")
senha = gerar_senha()
created = rc.create_user(
    name=nome,
    email=email,
    username=username,
    password=senha,
    require_password_change=True,
)
print("CRIADO id=", created.get("_id"), "username=", created.get("username"))
print("SENHA_TEMPORARIA=", senha)

try:
    room = rc.resolve_default_room(settings.rc_room_id, settings.rc_room_name or "SNAS")
    rc.invite_to_channel(room["_id"], created["_id"], room_type=room.get("t"))
    print("Convidado ao canal", room.get("name"), room["_id"])
except Exception as exc:
    print("Convite canal falhou:", exc)

cp = Checkpoint(settings.checkpoint_path)
cp.registrar(email, username, "created", user_id=created.get("_id"), detalhe="recreate-manual")
print("OK")
