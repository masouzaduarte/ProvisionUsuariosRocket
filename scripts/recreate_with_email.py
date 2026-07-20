import time
from src.config import load_settings
from src.rocketchat import RocketChatClient
from src.checkpoint import Checkpoint
from src.password import gerar_senha
from src.mailer import Mailer

settings = load_settings()
rc = RocketChatClient(settings.rc_base_url, settings.rc_admin_user, settings.rc_admin_password, timeout=180)
rc.login()
print("admin login OK")

username = "marco.memora"
email = "marco.duarte@memora.com.br"
nome = "Marco Antonio de Souza Duarte"

# Exclui SOMENTE por username (nunca por email genérico)
user = rc.find_by_username(username)
if user:
    uid = user["_id"]
    print(f"Excluindo {username} id={uid}")
    try:
        rc.delete_user(uid)
        print("delete OK")
    except Exception as exc:
        print(f"delete exception (pode ter excluido mesmo assim): {exc}")

    for i in range(8):
        time.sleep(2)
        if not rc.find_by_username(username):
            print(f"confirmado ausente apos check {i+1}")
            break
    else:
        if rc.find_by_username(username):
            raise SystemExit("Usuario ainda existe apos exclusao")
else:
    print("Username nao existe (ok)")

# limpa checkpoint
cp = Checkpoint(settings.checkpoint_path)
with cp._connect() as conn:
    conn.execute("DELETE FROM processados WHERE email = ? COLLATE NOCASE", (email,))
    conn.commit()

# recria + e-mail
senha = gerar_senha()
created = rc.create_user(
    name=nome,
    email=email,
    username=username,
    password=senha,
    require_password_change=True,
)
print("CRIADO", created.get("_id"), created.get("username"))

if settings.rc_room_id:
    rc.invite_to_channel(settings.rc_room_id, created["_id"])
    print("convidado ao canal")

mailer = Mailer(
    host=settings.smtp_host,
    port=settings.smtp_port,
    user=settings.smtp_user,
    password=settings.smtp_password,
    use_tls=settings.smtp_use_tls,
    from_addr=settings.smtp_from,
    from_name=settings.smtp_from_name,
    template_path=settings.template_path,
    app_url=settings.rc_base_url,
    dry_run=False,
)
mailer.send_password_email(to_email=email, nome=nome, username=username, senha=senha)
print("E-mail enviado para", email)

cp.registrar(email, username, "emailed", user_id=created.get("_id"))
print("SENHA_TEMPORARIA=", senha)
print("OK")
