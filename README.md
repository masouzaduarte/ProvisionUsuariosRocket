# ProvisionUsuariosRocket

Provisionamento em massa de usuários no **Rocket.Chat** a partir de CSV, em **duas fases**, com painel web.

## Fluxo em duas fases

| Fase | O que faz | E-mail |
|------|-----------|--------|
| **1 — Criar** | `users.create` com `sendWelcomeEmail: false` | **Nenhum** |
| **2 — Reset** | `users.forgotPassword` (SMTP do Rocket) | Só o e-mail de reset de senha |

Estado em SQLite (`data/checkpoint.db`) + export CSV:
- `data/usuarios_criados.csv`
- `data/usuarios_email_enviado.csv`

> Separado do `TesteCargaRocket` (k6). Aqui o foco é carga de usuários reais.

## Estrutura

```
ProvisionUsuariosRocket/
├── data/
│   ├── entrada.exemplo.csv
│   ├── entrada.csv
│   ├── checkpoint.db
│   ├── usuarios_criados.csv
│   └── usuarios_email_enviado.csv
├── web/                 # painel Flask + Bootstrap
├── src/
│   ├── phases.py        # fase 1, fase 2, exclusão segura
│   ├── safety.py        # usernames protegidos
│   ├── checkpoint.py
│   └── rocketchat.py
├── main.py              # CLI
├── run_web.py           # sobe o painel
└── scripts/delete_user.py
```

## CSV de entrada

```csv
nome,email,cpf
Marco Antonio de Souza Duarte,marco.duarte@memora.com.br,52998224725
```

- **Username no Rocket = CPF** (somente dígitos; a coluna `username` foi removida)
- `cpf` é **obrigatório** e validado
- Nomes das colunas configuráveis no `.env` (`CSV_COL_*`)

## Setup

```powershell
cd F:\PROJETOS_MC\ProvisionUsuariosRocket
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Edite .env (RC_ADMIN_USER / RC_ADMIN_PASSWORD / RC_BASE_URL)
Copy-Item data\entrada.exemplo.csv data\entrada.csv
```

## Painel web (recomendado)

```powershell
python run_web.py
```

Abra [http://127.0.0.1:5055](http://127.0.0.1:5055):

- Disparar **Fase 1** e **Fase 2** (com limite opcional)
- Listar criados / e-mails enviados / erros
- Enviar reset para um e-mail específico
- **Excluir usuário específico** (com confirmação digitada + recriar opcional)
- Exportar CSVs de estado

## CLI

```powershell
# Fase 1 — criar sem e-mail
python main.py --fase 1 --csv data\entrada.csv --limit 10

# Fase 2 — forgotPassword em massa
python main.py --fase 2 --limit 10

# Dry-run
python main.py --fase 1 --csv data\entrada.csv --limit 5 --dry-run
```

## Exclusão segura

**Nunca** exclui por e-mail. Só por **username** exato, com confirmação.

Bloqueados automaticamente:
- `admin`, `admin_mds`, `rocket.cat`, `bot`, `system`
- o `RC_ADMIN_USER` do `.env`
- qualquer usuário com role `admin` no Rocket
- a própria sessão autenticada

```powershell
python scripts/delete_user.py --username 52998224725 --confirm 52998224725
python scripts/delete_user.py --username 52998224725 --confirm 52998224725 --recreate
```

## Variáveis (`.env`)

| Variável | Descrição |
|----------|-----------|
| `RC_BASE_URL` | URL do Rocket.Chat |
| `RC_ADMIN_USER` / `RC_ADMIN_PASSWORD` | Conta com permissão de criar/excluir |
| `RC_ROOM_ID` | Opcional: convida ao canal após criar |
| `DELAY_MS` | Pausa entre usuários |

SMTP local **não é necessário** no fluxo em duas fases: o e-mail da Fase 2 usa o SMTP configurado no próprio Rocket.Chat.

## Template do e-mail de reset (Fase 2)

O texto está em `templates/forgot-password.html`.

Como a conta admin exige **TOTP**, aplique manualmente no Rocket:

1. **Administration** → **Workspace** → **Settings** → **Email**
2. Seção **Forgot Password** / **Esqueci a senha**
3. **Subject**:
   ```
   Defina sua senha — Rocket.Chat Ministério do Desenvolvimento Social
   ```
4. **Body** (cole o HTML de `templates/forgot-password.html`):

```html
<p>Olá, [name],</p>

<p>Sua conta no Rocket.Chat do Ministério do Desenvolvimento Social foi criada com sucesso.</p>

<p>Para acessar a plataforma, defina uma nova senha pelo link:<br>
<a href="[Forgot_Password_Url]">Definir nova senha</a></p>

<p>Depois, acesse: <a href="[Site_URL]">[Site_URL]</a></p>

<p>Se você não reconhece este cadastro, ignore este e-mail.</p>

<p>Atenciosamente,<br>
Equipe Rocket.Chat — Ministério do Desenvolvimento Social</p>
```

5. **Save changes**

Placeholders usados: `[name]`, `[Forgot_Password_Url]`, `[Site_URL]`.

Se no futuro a conta admin permitir API sem TOTP:

```powershell
python scripts/apply_forgot_password_email.py
```

- Exclusão só por username + confirmação + lista de proteção
- Senha gerada com `secrets`; Fase 1 não envia e-mail
- Não versionar `.env`, `checkpoint.db` nem CSVs com dados reais
