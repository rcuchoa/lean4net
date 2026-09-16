# Plan: Login de acesso + refresh visual profissional da interface web

## Summary
Adiciona uma tela de login (usuário/senha) na frente da interface web do
lean4net, usando Flask-Login + hash de senha via `werkzeug.security` e
credenciais configuradas por variável de ambiente (mesmo padrão já usado
para `ANTHROPIC_API_KEY`). Junto com o login, aplica um refresh visual no
restante da interface (header, botões, inputs) para um acabamento mais
"produto", reaproveitando a paleta escura já definida em `style.css`.

## User Story
Como usuário do lean4net rodando a interface web, quero uma tela de login
antes de acessar a ferramenta, para que o acesso ao verificador (e às
chamadas à API da Anthropic que ele dispara) fique restrito a quem tem
credenciais válidas, com uma aparência mais polida do que o protótipo
atual.

## Problem → Solution
**Atual**: `GET /` e todas as rotas `/api/*` são públicas — qualquer um com
acesso à porta 5000 usa a ferramenta e dispara chamadas pagas ao LLM, sem
nenhuma barreira. **Desejado**: `GET /` e `/api/*` exigem sessão
autenticada; usuário não autenticado é redirecionado (páginas) ou recebe
`401 JSON` (chamadas fetch) para uma tela de login com visual consistente
com o resto do app; após validar usuário/senha, cria sessão via
Flask-Login e libera acesso.

## Metadata
- **Complexity**: Medium
- **Source PRD**: N/A
- **PRD Phase**: N/A
- **Estimated Files**: 12

---

## UX Design

### Before
```
┌───────────────────────────────────────────┐
│ header: "lean4net" / subtítulo             │
├───────────────────────────────────────────┤
│  [YAML]     │ [Prompt/Lean gerado]         │
│             │                              │
├─────────────┴──────────────────────────────┤
│           [Resultado da verificação]        │
└───────────────────────────────────────────┘
Acesso direto, sem autenticação — abrir a URL já usa a ferramenta.
```

### After
```
GET /  (sem sessão) ──► redireciona para /login

┌───────────────────────────────┐
│          lean4net              │
│  verificador formal ... Lean 4 │
│                                 │
│   ┌─────────────────────────┐  │
│   │ Usuário  [___________]  │  │
│   │ Senha    [___________]  │  │
│   │ [ ] manter conectado    │  │
│   │      [ Entrar ]         │  │
│   │ (erro inline se falhar) │  │
│   └─────────────────────────┘  │
└───────────────────────────────┘
        │ login OK
        ▼
┌───────────────────────────────────────────┐
│ header: "lean4net" / subtítulo   usuário ⏻ │  <- área de sessão nova
├───────────────────────────────────────────┤
│  [YAML]     │ [Prompt/Lean gerado]         │  <- mesmos 3 painéis,
│             │                              │     botões/inputs com
├─────────────┴──────────────────────────────┤     acabamento visual
│           [Resultado da verificação]        │     refinado
└───────────────────────────────────────────┘
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `GET /` sem sessão | Serve `index.html` direto | Redireciona (302) para `/login` | via `@login_required` |
| `POST /api/*` sem sessão | Executa normalmente | `401 {"error": "nao autenticado"}` | handler customizado, não redireciona chamadas fetch |
| Header | Só título/subtítulo | Título/subtítulo + usuário logado + botão "Sair" | populado via `GET /api/whoami` no `app.js` |
| Login | Não existe | Tela dedicada, erro inline sem reload, opção "manter conectado" | `webapp/static/login.html` + `login.js` |
| Botões/inputs (geral) | Bordas retas, sem transição de foco | Cantos mais arredondados, `transition`, contorno de foco visível | `style.css`, aplica-se a login e app principal |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `webapp/server.py` | 1-58 | App Flask atual, `static_folder`/`static_url_path`, rota `index()` a proteger |
| P0 | `webapp/server.py` | 107-163 | Padrão de erro (`jsonify({"error": ...}), status`) a replicar em `/login` e `/api/whoami` |
| P0 | `webapp/server.py` | 229-232 | Padrão de env var (`os.environ.get("PORT", "5000")`) a replicar para as novas env vars |
| P1 | `webapp/static/style.css` | 1-12 | Tokens de cor (`:root`) que `login.css` deve reaproveitar, não duplicar |
| P1 | `webapp/static/style.css` | 195-219 | `.primary-btn` / `.status-badge` — padrão de botão e badge a polir mantendo a mesma estrutura de classes |
| P1 | `webapp/static/app.js` | 1-32 | `escapeHtml`, `setBadge` — helpers existentes a reaproveitar no novo código de header/login |
| P1 | `webapp/static/app.js` | 73-91 | Padrão de `fetch` com fallback em `catch` (`loadExample`) — mirror para `login.js` e `whoami` |
| P2 | `README.md` | 86-98 | Onde documentar as novas env vars, ao lado de `ANTHROPIC_API_KEY` |
| P2 | `verifier/lean_codegen.py` | 265-275 | Exemplo de mensagem de erro explicativa para credencial ausente — mirror para credencial de login ausente no startup |
| P2 | `ecosystem.config.cjs` | 1-19 | Config PM2 já criada nesta sessão — precisa propagar as novas env vars para `lean4net-webapp-5000` |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Auth para app Flask pequeno/interno | web search (2026) | Para uma ferramenta interna de usuário único, o padrão recomendado é **Flask-Login** para sessão + **`werkzeug.security.generate_password_hash`/`check_password_hash`** para a senha — não construir sessão/hash na mão, e não trazer um framework maior (Flask-Security etc.) para um caso de usuário único. |
| Armazenamento de credencial | web search (2026) | Guardar usuário + **hash** (nunca a senha em texto puro) em variável de ambiente é aceitável e comum para apps pequenas sem banco — mesmo padrão já usado neste projeto para `ANTHROPIC_API_KEY`. |
| Secret key de sessão | web search (2026) | `SECRET_KEY` deve vir de env var / secret manager, nunca hardcoded nem commitado. |

`werkzeug.security` já está disponível (dependência transitiva do Flask
instalado) — confirmado via `python3 -c "from werkzeug.security import
generate_password_hash, check_password_hash"`. `flask-login` **não** está
instalado ainda — precisa ser adicionado a `requirements.txt` e instalado.

---

## Patterns to Mirror

### NAMING_CONVENTION
// SOURCE: webapp/server.py:56-68
```python
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/example")
def example():
    ...
```
Rotas de página usam nome de substantivo (`index`), rotas de API usam
`api_<verbo>` (`api_prompt`, `api_generate`, `api_verify`). Novas rotas
devem seguir: `login` (view), `api_whoami` (API).

### ERROR_HANDLING
// SOURCE: webapp/server.py:81-90
```python
if not yaml_text.strip():
    return None, (jsonify({"error": "YAML vazio."}), 400)

try:
    sensitive_ports = [int(p) for p in ports_text.split(",") if p.strip()]
except ValueError:
    return None, (
        jsonify({"error": "Lista de portas sensiveis invalida (use algo como 22,3389)."}),
        400,
    )
```
Erros de request sempre viram `jsonify({"error": "<mensagem em pt-br,
minúscula, sem crase>"}), <status>` — nunca uma exceção crua. `/login`
(usuário/senha inválidos → 401) e `/api/whoami` sem sessão (não deveria
acontecer pois fica atrás de `@login_required`, mas por defesa retorna o
mesmo formato) devem seguir isso.

### ENV_VAR_PATTERN
// SOURCE: webapp/server.py:230
```python
port = int(os.environ.get("PORT", "5000"))
```
// SOURCE: verifier/lean_codegen.py:271 (mensagem de erro por credencial ausente)
```
"falha de autenticacao com a API da Anthropic -- defina ANTHROPIC_API_KEY "
```
Credenciais ausentes geram mensagem explicativa, não traceback. Aplicar o
mesmo a `LEAN4NET_ADMIN_USER` / `LEAN4NET_ADMIN_PASSWORD_HASH` ausentes:
falha no startup do servidor com mensagem clara em vez de subir com uma
conta padrão insegura.

### CSS_TOKENS
// SOURCE: webapp/static/style.css:1-12
```css
:root {
  --bg: #1e1e2e;
  --bg-panel: #242438;
  --bg-header: #181825;
  --border: #33334d;
  --text: #e0e0f0;
  --text-dim: #9090b0;
  --accent: #7aa2f7;
  --pass: #4ade80;
  --fail: #f87171;
  --mono: "SF Mono", "Fira Code", Consolas, Menlo, monospace;
}
```
`login.html` deve carregar `style.css` (pega os tokens + reset + estilo de
header "de graça") e depois `login.css` só com o necessário para o
cartão/formulário — nunca redefinir essas variáveis.

### BUTTON_PATTERN
// SOURCE: webapp/static/style.css:195-206
```css
.primary-btn {
  background: var(--accent);
  color: #10101a;
  border: none;
  padding: 6px 14px;
  border-radius: 4px;
  font-weight: 600;
  font-size: 0.8rem;
  cursor: pointer;
}
.primary-btn:hover { filter: brightness(1.1); }
.primary-btn:disabled { opacity: 0.5; cursor: not-allowed; }
```
O botão "Entrar" do login e o botão "Sair" do header reaproveitam
`.primary-btn` (e uma variante `.secondary-btn` nova para "Sair", texto em
vez de fundo sólido) — não criar um terceiro sistema de botão.

### JS_FETCH_PATTERN
// SOURCE: webapp/static/app.js:73-81
```javascript
async function loadExample() {
  try {
    const res = await fetch("/api/example");
    const data = await res.json();
    yamlInput.value = data.yaml || "";
  } catch (e) {
    yamlInput.value = "vpcs: []\nsubnets: []\nsecurity_groups: []\ninstances: []\n";
  }
}
```
`try/await fetch/catch` sem libs externas (sem axios, sem framework). O
`login.js` e o `whoami` fetch no `app.js` seguem exatamente essa forma.

### ESCAPE_HELPER
// SOURCE: webapp/static/app.js:14-19
```javascript
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
```
Qualquer texto vindo do servidor e inserido via `innerHTML` (mensagem de
erro de login, nome de usuário no header) passa por `escapeHtml` — mesma
regra já seguida em `renderResults`/`showError`.

### TEST_STRUCTURE
Não há testes automatizados para `webapp/` hoje (só `lean-api/tests/`
existe, fora do escopo deste plano). Este plano **não** introduz um
framework de teste novo — validação é manual (ver "Validação Manual"
abaixo), consistente com o padrão atual do módulo `webapp/`.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `webapp/auth.py` | CREATE | `LoginManager`, classe `User`, checagem de credencial via env var + hash, handler de não-autenticado |
| `webapp/hash_password.py` | CREATE | CLI mínima para gerar o hash a colocar em `LEAN4NET_ADMIN_PASSWORD_HASH` |
| `webapp/server.py` | UPDATE | Inicializa `LoginManager`, registra `/login` (GET+POST), `/logout`, `/api/whoami`; adiciona `@login_required` em `index()` e nas 3 rotas `/api/*` existentes |
| `webapp/static/login.html` | CREATE | Tela de login (usuário, senha, "manter conectado", erro inline) |
| `webapp/static/login.css` | CREATE | Estilo do cartão de login, reaproveitando tokens de `style.css` |
| `webapp/static/login.js` | CREATE | Submit via fetch para `POST /login`, erro inline, redirect em sucesso |
| `webapp/static/index.html` | UPDATE | Adiciona área de sessão no header (nome do usuário + botão "Sair") |
| `webapp/static/app.js` | UPDATE | Popula a área de sessão via `GET /api/whoami`; wire do botão "Sair" (`GET /logout`); trata `401` nas chamadas existentes como sessão expirada (redireciona para `/login`) |
| `webapp/static/style.css` | UPDATE | Refino de header (área de sessão), `.primary-btn`/inputs (raio, transição, foco), nova `.secondary-btn` |
| `requirements.txt` | UPDATE | Adiciona `flask-login>=0.6.3` |
| `README.md` | UPDATE | Documenta as 3 novas env vars, como gerar o hash, e o fluxo de login na seção "Interface web" |
| `ecosystem.config.cjs` | UPDATE | Propaga `LEAN4NET_ADMIN_USER`, `LEAN4NET_ADMIN_PASSWORD_HASH`, `LEAN4NET_SECRET_KEY` do ambiente do shell para o app `lean4net-webapp-5000` |

## NOT Building
- Cadastro de novos usuários pela interface (credenciais continuam fixas via env var, só um admin) — se precisar de mais de um usuário, é escopo de um plano futuro (arquivo de credenciais com múltiplas contas).
- Recuperação de senha / "esqueci minha senha".
- CSRF token dedicado no form de login — app roda só em `127.0.0.1`, single-user, sem esse vetor relevante hoje; registrar como risco aceito, não implementar.
- Rate limiting / bloqueio de tentativas de login.
- HTTPS/TLS — fora do escopo (app já roda só em `127.0.0.1`, sem mudança de exposição de rede).
- Alterar `debug=True` em `app.run(...)` — fora de escopo deste plano (é um risco pré-existente, não introduzido aqui); ver seção "Risks".
- Redesenho estrutural dos 3 painéis (layout, grid, funcionalidade) — só polimento visual (cores, raio, transições, header), não mudança de estrutura/UX das etapas 1-2-3.

---

## Step-by-Step Tasks

### Task 1: Criar `webapp/auth.py`
- **ACTION**: Novo módulo com `LoginManager`, classe `User` e checagem de credencial.
- **IMPLEMENT**:
  ```python
  from __future__ import annotations

  import os
  import sys

  from flask_login import LoginManager, UserMixin
  from werkzeug.security import check_password_hash

  ADMIN_USER = os.environ.get("LEAN4NET_ADMIN_USER")
  ADMIN_PASSWORD_HASH = os.environ.get("LEAN4NET_ADMIN_PASSWORD_HASH")

  if not ADMIN_USER or not ADMIN_PASSWORD_HASH:
      print(
          "erro: defina LEAN4NET_ADMIN_USER e LEAN4NET_ADMIN_PASSWORD_HASH "
          "antes de iniciar o servidor web (veja README.md -- gere o hash "
          "com `python3 webapp/hash_password.py`).",
          file=sys.stderr,
      )
      sys.exit(1)


  class User(UserMixin):
      def __init__(self, username: str) -> None:
          self.id = username


  login_manager = LoginManager()


  @login_manager.user_loader
  def load_user(user_id: str) -> User | None:
      return User(user_id) if user_id == ADMIN_USER else None


  def verify_credentials(username: str, password: str) -> User | None:
      if username == ADMIN_USER and check_password_hash(ADMIN_PASSWORD_HASH, password):
          return User(username)
      return None
  ```
- **MIRROR**: ENV_VAR_PATTERN (mensagem de erro explicativa + `sys.exit`, não exceção crua) e NAMING_CONVENTION.
- **IMPORTS**: `flask_login`, `werkzeug.security`.
- **GOTCHA**: `sys.exit(1)` roda no import do módulo — isso significa que `pm2 start` (ou `python3 webapp/server.py`) falha cedo e claro se as env vars não estiverem setadas, em vez de subir um servidor com uma conta padrão. Isso é intencional (mirror do comportamento de `ANTHROPIC_API_KEY` ausente), mas precisa estar documentado no README e no `ecosystem.config.cjs`.
- **VALIDATE**: `LEAN4NET_ADMIN_USER=x LEAN4NET_ADMIN_PASSWORD_HASH=y python3 -c "import webapp.auth"` não deve levantar erro nem sair; sem as env vars, deve imprimir a mensagem e sair com código 1.

### Task 2: Criar `webapp/hash_password.py`
- **ACTION**: CLI de uma linha para gerar o hash de uma senha.
- **IMPLEMENT**:
  ```python
  #!/usr/bin/env python3
  """Gera o hash de senha para LEAN4NET_ADMIN_PASSWORD_HASH.

  Uso:
      python3 webapp/hash_password.py 'minha-senha-forte'
  """
  from __future__ import annotations

  import sys

  from werkzeug.security import generate_password_hash


  def main() -> int:
      if len(sys.argv) != 2:
          print("uso: python3 webapp/hash_password.py '<senha>'", file=sys.stderr)
          return 2
      print(generate_password_hash(sys.argv[1]))
      return 0


  if __name__ == "__main__":
      sys.exit(main())
  ```
- **MIRROR**: Estilo de docstring/uso do cabeçalho de `main.py:1-19` e `webapp/server.py:1-19` (bloco `"""...Uso: ..."""` com exemplo de linha de comando).
- **IMPORTS**: `werkzeug.security`.
- **GOTCHA**: Nunca logar a senha em texto puro — o script só imprime o hash final.
- **VALIDATE**: `python3 webapp/hash_password.py teste123` imprime uma string iniciando com `pbkdf2:sha256:...` (ou algoritmo padrão do werkzeug instalado).

### Task 3: Atualizar `webapp/server.py` — proteger rotas e adicionar login/logout/whoami
- **ACTION**: Inicializar `login_manager`, registrar as novas rotas, proteger as existentes.
- **IMPLEMENT**:
  ```python
  import secrets

  from flask_login import current_user, login_required, login_user, logout_user
  from webapp.auth import login_manager, verify_credentials

  app.secret_key = os.environ.get("LEAN4NET_SECRET_KEY") or secrets.token_hex(32)
  if not os.environ.get("LEAN4NET_SECRET_KEY"):
      print(
          "aviso: LEAN4NET_SECRET_KEY nao definida -- usando uma chave "
          "temporaria (sessoes serao invalidadas a cada reinicio do servidor).",
          file=sys.stderr,
      )

  login_manager.init_app(app)


  @login_manager.unauthorized_handler
  def unauthorized():
      if request.path.startswith("/api/"):
          return jsonify({"error": "nao autenticado"}), 401
      return redirect(url_for("login"))


  @app.get("/login")
  def login():
      return send_from_directory(app.static_folder, "login.html")


  @app.post("/login")
  def login_submit():
      body = request.get_json(force=True, silent=True) or {}
      user = verify_credentials(body.get("username", ""), body.get("password", ""))
      if not user:
          return jsonify({"error": "usuario ou senha invalidos"}), 401
      login_user(user, remember=bool(body.get("remember")))
      return jsonify({"ok": True})


  @app.get("/logout")
  @login_required
  def logout():
      logout_user()
      return redirect(url_for("login"))


  @app.get("/api/whoami")
  @login_required
  def api_whoami():
      return jsonify({"username": current_user.id})


  @app.get("/")
  @login_required
  def index():
      return send_from_directory(app.static_folder, "index.html")
  ```
  E adicionar `@login_required` (import de `flask_login`) logo acima de
  `api_prompt`, `api_generate`, `api_verify` (mantendo a ordem/estrutura
  atual de cada função intacta).
- **MIRROR**: NAMING_CONVENTION (rotas de página vs `api_*`), ERROR_HANDLING (`jsonify({"error": ...}), 401`).
- **IMPORTS**: `secrets` (stdlib), `flask.redirect`, `flask.url_for`, `flask_login.{current_user, login_required, login_user, logout_user}`, `webapp.auth.{login_manager, verify_credentials}`.
- **GOTCHA**: `unauthorized_handler` precisa diferenciar `/api/*` (retorna 401 JSON, porque quem chama é `fetch()` e não navega) de `/` (redireciona, porque é navegação de página) — sem isso, uma sessão expirada em uma chamada `fetch` do `app.js` recebe um HTML de redirect em vez de JSON, quebrando o `await res.json()` existente em `runGenerate`/`runVerify`.
- **VALIDATE**: `curl -i http://127.0.0.1:5000/` sem cookie → `302` para `/login`; `curl -i http://127.0.0.1:5000/api/verify -X POST` sem cookie → `401` com corpo JSON `{"error": "nao autenticado"}`.

### Task 4: Criar `webapp/static/login.html`
- **ACTION**: Página de login com o mesmo header/branding do app principal.
- **IMPLEMENT**: Estrutura HTML com `<link rel="stylesheet" href="style.css">` + `<link rel="stylesheet" href="login.css">`, reaproveitando `<header><h1>lean4net</h1><p>...</p></header>` de `index.html:10-13`, seguido de um `<main class="login-main">` com um `<form id="login-form">`: campos `username`, `password`, checkbox `remember` ("manter conectado"), botão `.primary-btn` "Entrar", e uma `<div id="login-error" class="error-box" hidden>` para erro inline (reaproveita a classe `.error-box` já existente em `style.css:257-266`).
- **MIRROR**: Estrutura de `webapp/static/index.html:1-14` (head/header) e classe `.error-box` de `style.css`.
- **IMPORTS**: N/A (HTML estático).
- **GOTCHA**: Este arquivo é servido tanto por `GET /login` (rota explícita em `server.py`) quanto, por causa de `static_url_path=""`, também ficaria acessível diretamente em `/login.html` — sem problema de segurança (é a própria tela pública de login), mas o `login.js` deve sempre fazer `fetch("/login", {method: "POST", ...})`, não `fetch("/login.html", ...)`.
- **VALIDATE**: Abrir `http://127.0.0.1:5000/login` no navegador mostra o formulário estilizado, sem console errors.

### Task 5: Criar `webapp/static/login.css`
- **ACTION**: Estilo do cartão de login.
- **IMPLEMENT**: `.login-main` centraliza um `.login-card` (`max-width: 340px`, `background: var(--bg-panel)`, `border: 1px solid var(--border)`, `border-radius: 10px`, padding generoso, `box-shadow` sutil), inputs de texto com o mesmo tratamento de `#ports-input` (`style.css:184-193`) mas full-width, label acima de cada campo, checkbox alinhado com o texto "manter conectado".
- **MIRROR**: CSS_TOKENS (usa só `var(--bg)`, `var(--bg-panel)`, `var(--border)`, `var(--text)`, `var(--text-dim)`, `var(--accent)` — nunca hardcode cor nova) e BUTTON_PATTERN para o botão "Entrar".
- **IMPORTS**: N/A.
- **GOTCHA**: Não redefinir `:root` aqui — `login.html` já carrega `style.css` antes, que é quem define os tokens.
- **VALIDATE**: Visualmente consistente com o tema escuro do app principal (mesma cor de fundo/acento), responsivo em largura de celular (~400px, sem overflow horizontal).

### Task 6: Criar `webapp/static/login.js`
- **ACTION**: Submissão do form via fetch, erro inline, redirect em sucesso.
- **IMPLEMENT**:
  ```javascript
  const form = document.getElementById("login-form");
  const errorBox = document.getElementById("login-error");
  const submitBtn = document.getElementById("login-submit");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorBox.hidden = true;
    submitBtn.disabled = true;

    const body = JSON.stringify({
      username: form.username.value,
      password: form.password.value,
      remember: form.remember.checked,
    });

    try {
      const res = await fetch("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      const payload = await res.json();
      if (!res.ok) {
        errorBox.textContent = payload.error || "falha ao entrar";
        errorBox.hidden = false;
        return;
      }
      window.location.href = "/";
    } catch (err) {
      errorBox.textContent = `Falha ao conectar com o servidor: ${err.message}`;
      errorBox.hidden = false;
    } finally {
      submitBtn.disabled = false;
    }
  });
  ```
- **MIRROR**: JS_FETCH_PATTERN (`try/await fetch/catch`) e `showError`-style de `app.js:137-141` (mensagem de erro de conexão).
- **IMPORTS**: N/A (vanilla JS, sem libs — mesmo padrão de `app.js`).
- **GOTCHA**: Usar `payload.error` (texto já vem do servidor em pt-br) em vez de `escapeHtml` + `innerHTML` — aqui é `textContent`, então não há risco de XSS mesmo sem escapar.
- **VALIDATE**: Submeter com senha errada mostra a caixa de erro sem recarregar a página; submeter certo redireciona para `/`.

### Task 7: Atualizar `webapp/static/index.html` — área de sessão no header
- **ACTION**: Adicionar bloco de usuário logado + botão "Sair" ao header existente.
- **IMPLEMENT**: Dentro de `<header>` (linhas 10-13 atuais), envolver o conteúdo existente e adicionar:
  ```html
  <header>
    <div class="header-title">
      <h1>lean4net</h1>
      <p>verificador formal de infraestrutura de nuvem &mdash; Lean 4</p>
    </div>
    <div class="header-session">
      <span id="session-user" class="session-user"></span>
      <a id="logout-link" href="/logout" class="secondary-btn">Sair</a>
    </div>
  </header>
  ```
- **MIRROR**: Indentação/estilo de marcação de `index.html:9-13`.
- **IMPORTS**: N/A.
- **GOTCHA**: Precisa de `display: flex; justify-content: space-between;` no `header` (hoje só tem padding/borda) — ajustar em `style.css`, não aqui.
- **VALIDATE**: Header renderiza com o título à esquerda e (depois do Task 8) usuário+Sair à direita, sem quebrar layout em mobile.

### Task 8: Atualizar `webapp/static/app.js` — popular sessão e tratar 401
- **ACTION**: Buscar usuário logado ao carregar a página; tratar `401` como sessão expirada nas chamadas existentes.
- **IMPLEMENT**:
  ```javascript
  async function loadSession() {
    try {
      const res = await fetch("/api/whoami");
      if (res.status === 401) {
        window.location.href = "/login";
        return;
      }
      const data = await res.json();
      document.getElementById("session-user").textContent = data.username || "";
    } catch (e) {
      // sem sessao visivel nao bloqueia o uso da pagina em si
    }
  }
  loadSession();
  ```
  E em `runGenerate`/`runVerify` (após cada `fetch`), adicionar checagem:
  ```javascript
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  ```
  logo antes de `const payload = await res.json();` nos dois lugares (`app.js:165` e `app.js:220`).
- **MIRROR**: JS_FETCH_PATTERN e a chamada existente `loadExample()` no fim do arquivo (`app.js:235`) — `loadSession()` é adicionada do mesmo jeito, uma chamada solta ao final do setup de listeners.
- **IMPORTS**: N/A.
- **GOTCHA**: Como `index()` já está atrás de `@login_required` (Task 3), na prática `app.js` só roda com sessão válida no carregamento inicial — o tratamento de `401` aqui cobre o caso de a sessão **expirar enquanto a página já está aberta** (o usuário continua na aba depois do cookie expirar).
- **VALIDATE**: Com sessão ativa, header mostra o nome do usuário; apagando o cookie de sessão manualmente (devtools) e clicando "Gerar Lean", a página redireciona para `/login` em vez de mostrar um erro genérico de JSON inválido.

### Task 9: Atualizar `webapp/static/style.css` — polimento visual geral
- **ACTION**: Refino de header, botões, inputs e nova classe `.secondary-btn`, mantendo os tokens de `:root` existentes.
- **IMPLEMENT**:
  ```css
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .header-session { display: flex; align-items: center; gap: 10px; }
  .session-user { font-size: 0.8rem; color: var(--text-dim); }

  .secondary-btn {
    background: transparent;
    color: var(--text-dim);
    border: 1px solid var(--border);
    padding: 5px 12px;
    border-radius: 6px;
    font-size: 0.78rem;
    text-decoration: none;
    transition: border-color 0.15s, color 0.15s;
  }
  .secondary-btn:hover { border-color: var(--accent); color: var(--text); }

  .primary-btn,
  .file-btn,
  #ports-input,
  .secondary-btn {
    border-radius: 6px;
    transition: filter 0.15s, border-color 0.15s, box-shadow 0.15s;
  }
  .primary-btn:focus-visible,
  .file-btn:focus-visible,
  #ports-input:focus-visible,
  .secondary-btn:focus-visible,
  input:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  ```
- **MIRROR**: CSS_TOKENS (só reaproveita variáveis existentes) e BUTTON_PATTERN (estende, não substitui, `.primary-btn`).
- **IMPORTS**: N/A.
- **GOTCHA**: `border-radius: 6px` sobrescreve o `4px` atual de `.primary-btn`/`.file-btn`/`#ports-input` — confirmar visualmente que não estoura o alinhamento dos badges ao lado (`.status-badge` mantém `border-radius: 12px`, não é afetado).
- **VALIDATE**: `@media (max-width: 800px)` (já existente em `style.css:268-278`) continua funcionando — header não quebra em telas estreitas (usuário+Sair podem quebrar linha via `flex-wrap: wrap`, já incluso acima).

### Task 10: Atualizar `requirements.txt`
- **ACTION**: Adicionar a nova dependência.
- **IMPLEMENT**: Acrescentar a linha `flask-login>=0.6.3` (mesmo estilo `pacote>=versao` das linhas existentes).
- **MIRROR**: Formato exato das 3 linhas atuais (`pyyaml>=6.0`, `flask>=3.0`, `anthropic>=0.69.0`).
- **IMPORTS**: N/A.
- **GOTCHA**: Rodar `pip install -r requirements.txt` depois de editar, senão `webapp/auth.py` falha com `ModuleNotFoundError` (confirmado nesta sessão: `flask_login` ainda não está instalado no ambiente).
- **VALIDATE**: `python3 -c "import flask_login"` não levanta erro após a instalação.

### Task 11: Atualizar `README.md`
- **ACTION**: Documentar as env vars novas e o fluxo de login na seção "Interface web".
- **IMPLEMENT**: Logo após o bloco de pré-requisitos existente (`README.md:88-94`, que já documenta `ANTHROPIC_API_KEY`), adicionar as 3 novas variáveis com o mesmo formato de lista (`LEAN4NET_ADMIN_USER`, `LEAN4NET_ADMIN_PASSWORD_HASH` — gerado via `python3 webapp/hash_password.py '<senha>'` —, `LEAN4NET_SECRET_KEY`), e uma frase na seção "Interface web" (`README.md:134-166`) descrevendo que o acesso agora requer login.
- **MIRROR**: Estilo de prosa/formatação das seções existentes (lista com `-`, blocos ```bash``` para comandos).
- **IMPORTS**: N/A.
- **GOTCHA**: Manter a tabela de rotas existente (`README.md:152-156`) e só acrescentar `POST /login`, `GET /logout`, `GET /api/whoami` nela, sem reescrever as linhas já existentes.
- **VALIDATE**: Seguir o README do zero (env vars + comandos) é suficiente para logar com sucesso, sem precisar olhar o código.

### Task 12: Atualizar `ecosystem.config.cjs`
- **ACTION**: Propagar as 3 novas env vars para o app `lean4net-webapp-5000` gerenciado pelo PM2.
- **IMPLEMENT**:
  ```javascript
  {
    name: 'lean4net-webapp-5000',
    cwd: __dirname,
    script: 'webapp/server.py',
    interpreter: 'python3',
    env: {
      PORT: '5000',
      LEAN4NET_ADMIN_USER: process.env.LEAN4NET_ADMIN_USER,
      LEAN4NET_ADMIN_PASSWORD_HASH: process.env.LEAN4NET_ADMIN_PASSWORD_HASH,
      LEAN4NET_SECRET_KEY: process.env.LEAN4NET_SECRET_KEY,
    },
  },
  ```
- **MIRROR**: Estrutura atual do arquivo (criado nesta mesma sessão) — só estende o bloco `env` do primeiro app, sem tocar no app `lean4net-api-8000`.
- **IMPORTS**: N/A.
- **GOTCHA**: PM2 herda `process.env` do shell **que roda `pm2 start`**, não do shell atual da sessão — o usuário precisa ter essas 3 variáveis exportadas no shell antes de rodar `pm2 start ecosystem.config.cjs` (ou `pm2 restart lean4net-webapp-5000 --update-env`), senão `webapp/auth.py` (Task 1) derruba o processo no boot e o PM2 fica em crash-loop (mesmo sintoma observado nesta sessão com a porta 8000 ocupada, mas por causa diferente — aqui é env var ausente, não porta em uso).
- **VALIDATE**: Com as 3 env vars exportadas, `pm2 restart lean4net-webapp-5000 --update-env && pm2 logs lean4net-webapp-5000 --lines 20 --nostream` não mostra o erro de credencial ausente nem crash-loop (`↺` parado em 0).

---

## Testing Strategy

Não há suíte automatizada para `webapp/` no projeto hoje (Golden Rule:
não introduzir um framework de teste novo fora do escopo pedido). A
validação é funcional/manual, task a task (ver `VALIDATE` de cada task
acima) mais o checklist manual abaixo.

### Edge Cases Checklist
- [ ] Usuário/senha em branco → `401` com mensagem clara, sem 500.
- [ ] Usuário certo, senha errada → `401`, mesma mensagem genérica (não revelar se o usuário existe).
- [ ] Sessão expira com a aba aberta em `/` → próxima ação (`Gerar Lean`/`Verificar`) redireciona para `/login` em vez de travar num erro de parsing JSON.
- [ ] `LEAN4NET_ADMIN_USER`/`LEAN4NET_ADMIN_PASSWORD_HASH` ausentes → servidor recusa subir com mensagem clara (não um servidor "aberto" por acidente).
- [ ] `LEAN4NET_SECRET_KEY` ausente → servidor sobe (com aviso no stderr), mas sessões não sobrevivem a um restart do processo — aceitável para dev, documentado no README.
- [ ] Viewport mobile (~400px) na tela de login e no header com a nova área de sessão — sem scroll horizontal.

---

## Validation Commands

### Static Analysis
```bash
python3 -m py_compile webapp/server.py webapp/auth.py webapp/hash_password.py
```
EXPECT: Sem erro de sintaxe (projeto não usa mypy/ruff configurado — não introduzir um novo aqui).

### Instalação da dependência nova
```bash
pip install -r requirements.txt
python3 -c "import flask_login"
```
EXPECT: Importa sem erro.

### Servidor sobe com credenciais
```bash
export LEAN4NET_ADMIN_USER=admin
export LEAN4NET_ADMIN_PASSWORD_HASH=$(python3 webapp/hash_password.py 'senha-de-teste')
export LEAN4NET_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
python3 webapp/server.py &
sleep 1
curl -i http://127.0.0.1:5000/            # espera 302 -> /login
curl -i http://127.0.0.1:5000/api/verify -X POST  # espera 401 JSON
```
EXPECT: `/` redireciona; `/api/verify` sem sessão retorna `401` JSON (não HTML).

### Servidor recusa subir sem credenciais
```bash
unset LEAN4NET_ADMIN_USER LEAN4NET_ADMIN_PASSWORD_HASH
python3 webapp/server.py; echo "exit=$?"
```
EXPECT: Mensagem explicativa no stderr, `exit=1`.

### Browser Validation
```bash
python3 webapp/server.py
# abrir http://127.0.0.1:5000/login manualmente
```
EXPECT: Tela de login estilizada; login com credenciais corretas leva ao app principal com o header mostrando o usuário e o botão "Sair"; "Sair" volta para `/login`.

### Manual Validation
- [ ] Login com credencial errada mostra erro inline, sem reload de página.
- [ ] Login com credencial certa + "manter conectado" mantém sessão após fechar/reabrir o navegador (cookie persistente).
- [ ] Fluxo completo YAML → Gerar Lean → Verificar continua funcionando igual, só que agora atrás de login (regressão zero na funcionalidade existente).
- [ ] Visual do login e do header combina com o resto do app (mesma paleta, tipografia, cantos arredondados).

---

## Acceptance Criteria
- [ ] Todas as 12 tasks concluídas.
- [ ] Todos os comandos de validação passam.
- [ ] `GET /` e as 3 rotas `/api/*` de negócio exigem sessão autenticada.
- [ ] Tela de login com visual consistente com o app (mesmos tokens de cor).
- [ ] Sem regressão no fluxo YAML → Lean → Verificação.
- [ ] README documenta as env vars novas e como gerar o hash.

## Completion Checklist
- [ ] Código segue os padrões descobertos (naming, erro, env var, CSS tokens).
- [ ] Nenhum valor hardcoded (usuário/senha/secret sempre via env var).
- [ ] `ecosystem.config.cjs` atualizado para não crash-loopar por falta de env var.
- [ ] Sem escopo extra: nenhum redesenho estrutural dos 3 painéis, nenhum CSRF/rate-limit/multi-usuário adicionados além do combinado.
- [ ] Self-contained — nenhuma dúvida nova surge durante a implementação.

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `app.run(..., debug=True)` continua ligado (pré-existente) — expõe o debugger interativo do Werkzeug (RCE) se a porta 5000 for exposta além de `127.0.0.1` | Baixa (app já só escuta em `127.0.0.1`) | Alta se a exposição de rede mudar no futuro | Fora de escopo deste plano; registrar como risco conhecido, não desligar aqui para não misturar mudanças |
| `LEAN4NET_SECRET_KEY` não configurada em produção/PM2 | Média | Sessões caem a cada restart do processo (incômodo, não é falha de segurança) | Task 12 propaga a env var pro PM2; README (Task 11) deixa claro que é obrigatória para sessão persistente |
| Ausência de CSRF token no form de login | Baixa (single-user, `127.0.0.1`) | Baixo no contexto atual | Documentado em "NOT Building"; revisar se o app algum dia for exposto além de localhost |
| `pm2 start` sem as env vars exportadas no shell que o invoca | Média (fácil de esquecer) | Processo entra em crash-loop (mesmo padrão observado nesta sessão com a porta 8000) | GOTCHA explícito na Task 12 + `pm2 logs` deixa o erro de credencial ausente visível de imediato |

## Notes
- Este plano assume um único usuário admin (decisão explícita do usuário
  durante o planejamento, após pesquisa confirmando que Flask-Login +
  hash via env var é o padrão recomendado para esse porte de app). Uma
  extensão para múltiplos usuários fixos (arquivo de credenciais) foi
  cogitada e descartada por ora — ver "NOT Building".
- `ecosystem.config.cjs` e os comandos `.claude/commands/pm2-*.md` já
  existem nesta sessão (setup anterior do PM2); este plano só estende o
  bloco `env` do app `lean4net-webapp-5000`, não recria a config.
