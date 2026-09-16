# lean4net — verificador formal de infraestrutura de nuvem

Recebe uma especificação simples de infraestrutura de nuvem virtual (YAML),
usa um **LLM da Anthropic (Claude)** para gerar um programa em **Lean 4**
que modela essa infraestrutura e formula um conjunto de propriedades como
proposições *decidíveis*, e usa o próprio **compilador Lean** para validar
matematicamente se as propriedades são verdadeiras.

Se o Lean aceita o arquivo gerado (exit code 0), cada propriedade foi
verificada — não testada em alguns casos, mas provada para todos os
valores da especificação, porque o kernel do Lean avaliou a função
booleana correspondente sobre os dados exatos da spec e conferiu que o
resultado é `true`. Importante: quem gera o texto Lean é o modelo, não
código determinístico — o **compilador Lean continua sendo a autoridade
final**. Se o modelo errar a tradução da spec ou a semântica de uma
propriedade, o `theorem` correspondente simplesmente falha a elaborar e
aparece como FAIL no relatório.

## Estrutura do projeto

```
lean4net/
├── frontend/          # HTML/CSS/JS estático (login + as 4 zonas)
├── backend/
│   ├── core/            # pipeline de domínio (spec_loader/lean_codegen/runner), usado por main.py e webapp/
│   ├── webapp/           # servidor Flask (login + API JSON), serve frontend/
│   └── lean-api/          # serviço FastAPI independente, desacoplado do core
├── data/
│   ├── spec/             # entrada: specs YAML de exemplo
│   └── build/             # saída: .lean gerados/verificados
├── main.py             # CLI, ponto de entrada do usuário
├── docs/               # documentação (ver ARQUITETURA.md)
├── Dockerfile           # imagem única (Python + Lean 4), ver "Docker" abaixo
└── docker-compose.yml    # webapp + lean-api + cli, a partir da mesma imagem
```

| Camada | Caminho | Conteúdo |
|---|---|---|
| **Backend** | `backend/core/`, `main.py`, `backend/webapp/`, `backend/lean-api/` | Pipeline core (Python), CLI, servidor Flask e a API FastAPI independente — só código de servidor/CLI, nenhum HTML/CSS/JS |
| **Frontend** | `frontend/` | HTML/CSS/JS estático da interface web (login + as 4 zonas), servido pelo `backend/webapp/` |
| **Banco de dados** | — | Não existe nenhum componente de banco de dados neste projeto |
| Entrada/saída | `data/spec/`, `data/build/` | Specs YAML de exemplo e os `.lean` gerados/verificados |

Ver [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) para a visão completa de
como os quatro componentes de backend se relacionam.

## Como funciona (pipeline em duas etapas)

A execução é dividida em duas etapas independentes, para que o código Lean
gerado possa ser **inspecionado (e até corrigido) antes** de ser submetido
ao compilador:

```
ETAPA 1 — geração (chama o LLM, não chama o Lean)
data/spec/*.yaml  --[spec_loader.py]-->  modelo Python (CIDRs já parseados)
             --[lean_codegen.py]--> Claude escreve data/build/*.lean (dados + teoremas `by decide`)
             --> o .lean é gravado e apresentado

ETAPA 2 — verificação (chama o Lean, não chama o LLM)
data/build/*.lean --[lean_codegen.parse_properties]--> intervalos de linha por propriedade
             --[runner.py]--------> `lean data/build/*.lean`  (compilador Lean 4)
             --> relatório PASS/FAIL por propriedade
```

As duas etapas não compartilham estado: o único elo entre elas é o próprio
arquivo `.lean`, de onde a etapa 2 recalcula os marcadores
(`-- @property:PX`) sobre o texto atual. Consequências práticas: dá para
revisar/editar o Lean no meio do caminho, reverificar quantas vezes quiser
sem pagar uma nova chamada à API, e gerar em uma máquina e verificar em
outra (só a etapa 2 exige o Lean instalado; só a etapa 1 exige credenciais
da Anthropic).

- **`backend/core/model.py`** — dataclasses da infraestrutura (Vpc, Subnet,
  SecurityGroup, Rule, Instance) e parsing de CIDR (`"10.0.1.0/24"` →
  inteiro de 32 bits + tamanho de prefixo); também `CustomProperty` (id +
  descrição em linguagem natural), ver "Propriedades customizadas".
- **`backend/core/spec_loader.py`** — lê o YAML e valida a estrutura (campos
  obrigatórios, CIDRs bem formados, nomes únicos) antes de gerar qualquer
  coisa; `load_custom_properties()` faz a validação equivalente para a
  lista de propriedades customizadas (id no formato `C<número>`, descrição
  não vazia, sem duplicatas).
- **`backend/core/lean_codegen.py`** — monta um prompt com a especificação (já
  convertida para JSON com CIDRs em `addr`/`plen`) e a semântica exata de
  cada propriedade, chama a API da Anthropic (`claude-opus-5`) e recebe de
  volta o `.lean` completo: a biblioteca de aritmética de CIDR, os dados da
  spec como termos Lean e um `theorem ... := by decide` por propriedade. O
  modelo é instruído a envolver cada teorema com marcadores
  (`-- @property:C1` / `-- @end_property:C1`); o intervalo de linhas de
  cada propriedade é calculado em Python a partir do texto realmente
  devolvido (nunca de um número de linha que o modelo alegue), para depois
  ligar erros do compilador de volta à propriedade correspondente. Requer
  credenciais da Anthropic configuradas (`ANTHROPIC_API_KEY` ou um login
  via `ant auth login`). Qualquer falha na chamada (autenticação, rede,
  recusa do modelo, credenciais ausentes) vira `LeanCodegenError`, nunca uma
  exceção crua — ver "Segurança e limitações" abaixo.
  `parse_properties()` (o cálculo dos intervalos) é público justamente
  porque a etapa 2 o chama de novo sobre o arquivo em disco. Não há mais
  propriedades fixas embutidas: `generate()`/`build_prompt()` recebem a
  lista completa de `CustomProperty` a provar (ver "Propriedades
  customizadas") e recusam gerar se ela vier vazia; `DEFAULT_PROPERTIES`
  é só o texto usado para pré-popular o editor, sem tratamento especial no
  gerador em si.
- **`backend/core/runner.py`** — chama o binário `lean` sobre o arquivo
  gerado e faz o parsing das mensagens `arquivo:linha:coluna: error: ...`
  para descobrir qual propriedade falhou.
- **`main.py`** — CLI com os dois subcomandos (`generate` e `verify`) e o
  relatório.

## Propriedades customizadas

Não há um conjunto fixo de propriedades embutido no gerador: **todas** —
inclusive as quatro originais deste projeto — são definidas **em linguagem
natural** e podem ser editadas, apagadas ou complementadas livremente. O
modelo formaliza cada descrição como um `theorem` Lean próprio, sempre
seguindo a mesma regra (enunciado sempre positivo, `by decide`/`by
native_decide`, nunca reescrito como negação — ver "Segurança e
limitações" abaixo). Não há persistência (nenhum componente deste projeto
usa banco de dados): a lista de propriedades é fornecida a cada chamada,
seja via `--properties` na CLI ou via o editor na interface web.

O editor (CLI e webapp) vem **pré-populado** com as quatro descrições
originais do projeto — a partir daí, funcionam exatamente como qualquer
propriedade adicionada depois:

| ID pré-populado | Descrição padrão |
|----|-----|
| C1 | Toda subnet está contida dentro do CIDR da sua própria VPC. |
| C2 | Nenhum par de subnets da mesma VPC tem faixas de IP sobrepostas. |
| C3 | Nenhuma regra de ingress libera uma porta sensível (22, 3389 por padrão) para `0.0.0.0/0`. |
| C4 | Toda instância referencia uma subnet e security groups que de fato existem na spec. |

O veredito é **por propriedade** (não tudo-ou-nada): se só uma falhar, as
demais ainda são reportadas como provadas independentemente, porque cada
uma é um `theorem` Lean separado.

- **CLI**: `--properties <arquivo.json>` em `generate` e `verify`, com uma
  lista `[{"id": "C1", "description": "..."}]` — o `id` segue o formato
  `C<número>` e o nome do teorema correspondente é sempre
  `customProp_<id>` (nunca escolhido pelo usuário, para que `verify`
  consiga reconhecer o marcador mesmo sem o arquivo de propriedades à
  mão — nesse caso a descrição aparece como "(propriedade customizada —
  descrição original não fornecida nesta verificação)" no relatório, mas a
  prova em si não é afetada). Sem `--properties`, `generate` usa as quatro
  descrições padrão da tabela acima; `verify` não assume nenhuma (só
  melhora as descrições no relatório se apontado para o mesmo arquivo).
- **Webapp**: painel "Propriedades customizadas" dentro da área do YAML,
  pré-populado ao carregar a página (`GET /api/default-properties`) —
  adiciona, edita in-line ou remove descrições, com o `id` atribuído
  automaticamente. Enviado como `custom_properties` no corpo de
  `/api/prompt`, `/api/generate` e `/api/verify`, junto com o YAML. Gerar
  com a lista vazia é um erro explícito (nada para provar).

```bash
echo '[{"id": "C1", "description": "Nenhuma instancia na subnet publica tem a porta 3389 aberta"}]' > propriedades.json
python3 main.py generate data/spec/example.yaml --properties propriedades.json
python3 main.py verify data/build/example.lean --properties propriedades.json
```

## Uso

Pré-requisitos:
- Lean 4 instalado via [elan](https://github.com/leanprover/elan)
  (`lean --version` deve funcionar). Nenhuma dependência de Mathlib é
  necessária — só `Init`, que já vem com o toolchain.
- Credenciais da Anthropic configuradas: variável de ambiente
  `ANTHROPIC_API_KEY`, ou um login prévio via `ant auth login`. Sem isso, a
  geração do Lean 4 falha com um erro explicativo antes de chamar o Lean.
- Para a interface web (ver "Interface web" abaixo), 3 variáveis de
  ambiente adicionais controlam o login: `LEAN4NET_ADMIN_USER`,
  `LEAN4NET_ADMIN_PASSWORD_HASH` (gerado com `python3
  backend/webapp/hash_password.py '<senha>'`, nunca a senha em texto puro) e
  `LEAN4NET_SECRET_KEY` (chave de sessão do Flask). Sem as duas primeiras,
  `backend/webapp/server.py` recusa subir.

```bash
pip install -r requirements.txt   # inclui pyyaml, flask-login e o SDK `anthropic`
```

**Etapa 1 — gerar o Lean 4** (grava em `data/build/<nome-da-spec>.lean` e
imprime o código):

```bash
python3 main.py generate data/spec/example.yaml
python3 main.py generate data/spec/example.yaml \
  --out data/build/minha_infra.lean \
  --sensitive-ports 22,3389,3306
```

O código Lean sai em `stdout` e as mensagens de progresso em `stderr`, então
`python3 main.py generate data/spec/example.yaml > infra.lean` também funciona.

**Etapa 2 — verificar com o compilador Lean 4:**

```bash
python3 main.py verify data/build/example.lean            # tudo passa
python3 main.py verify data/build/example_bad.lean        # tudo falha (violações propositais)
python3 main.py verify data/build/example_partial.lean    # só a P3 falha (SSH aberto ao mundo)

python3 main.py verify data/build/example.lean --lean-bin /caminho/para/lean
```

Códigos de saída: `generate` devolve `0` (gerou) ou `2` (erro de spec, de
credenciais ou de formato da resposta do modelo); `verify` devolve `0`
(todas provadas), `1` (alguma propriedade falhou) ou `2` (Lean ausente,
arquivo ilegível ou sem os marcadores esperados).

O `.lean` pode ser aberto/inspecionado normalmente entre as duas etapas (ou
aberto no VS Code com a extensão do Lean 4 para ver os teoremas provados
interativamente) — inclusive editado, já que `verify` sempre relê o arquivo
do disco. `verify` não passa por `--sensitive-ports`: a lista é lida do
próprio `def sensitivePorts` do arquivo verificado.

## Interface web

O acesso à interface web requer login (usuário/senha, ver pré-requisitos
acima) — `GET /` e as rotas `/api/*` redirecionam (páginas) ou respondem
`401` (chamadas fetch) até a sessão ser autenticada em `/login`. Uma vez
logado, também há uma interface web (backend em `backend/webapp/`, frontend estático
em `frontend/`) com 4 zonas, em duas colunas: à esquerda, **1. Propriedades**
(ver seção acima, sempre visível — não colapsa mais) e **2. Especificação
da infraestrutura (YAML)** (editável, com upload de arquivo, e o botão
**"Gerar Lean"**); à direita, **3. AI Prompt | Código Lean 4** (duas
abas, a segunda **editável**, com o botão **"Verificar"**) e **4.
Resultado da verificação**. Cada coluna tem seu redimensionador vertical
independente, além do redimensionador que separa as duas colunas.

Ao clicar em "Gerar Lean", o painel do meio mostra **primeiro o prompt que
será enviado ao modelo** (parâmetros da requisição + system prompt + user
prompt com a spec em JSON) e só troca para o código quando ele chega; a aba
"Prompt" continua guardando o que foi enviado. Como é o texto do painel que
vai para o compilador, editar o Lean ali e clicar em "Verificar" reverifica
a versão editada, sem nova chamada ao modelo.

O servidor Flask reaproveita o mesmo pipeline de `main.py` (`spec_loader` →
`lean_codegen` → `runner`), sem duplicar lógica, e expõe as etapas como
endpoints separados e sem estado:

| Rota | Corpo → resposta | Custo |
|---|---|---|
| `POST /login` | `{username, password, remember}` → `{ok}` ou `401 {error}` | nenhum |
| `GET /logout` | — → redireciona para `/login` | nenhum |
| `GET /api/whoami` | — → `{username}` | nenhum |
| `POST /api/prompt` | `{yaml, sensitive_ports, custom_properties}` → `{model, max_tokens, thinking, output_config, system, user}` | nenhum (não chama o LLM) |
| `POST /api/generate` | `{yaml, sensitive_ports, custom_properties}` → `{lean_source, properties}` | uma chamada à API da Anthropic |
| `POST /api/verify` | `{lean_source, custom_properties}` → `{report}` | uma execução do `lean` |

`custom_properties` (`[{"id": "C1", "description": "..."}]`, ver
"Propriedades customizadas" acima) não persiste no servidor — o front-end
reenvia a lista atual do editor em cada chamada. O campo em si é opcional
na requisição (ausente equivale a lista vazia), mas `/api/generate` recusa
gerar se a lista ficar vazia — precisa de ao menos uma propriedade.

O `/api/prompt` monta a requisição com a mesma função (`build_prompt()`) que
o `/api/generate` usa para chamar o modelo, então o prompt exibido não tem
como divergir do que é enviado.

```bash
pip install -r requirements.txt
export LEAN4NET_ADMIN_USER=admin
export LEAN4NET_ADMIN_PASSWORD_HASH=$(python3 backend/webapp/hash_password.py 'sua-senha')
export LEAN4NET_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
python3 backend/webapp/server.py
# abre http://127.0.0.1:5000 -- pede login antes de liberar a interface
```

## Docker

Uma imagem única (`Dockerfile`, na raiz) contém tudo -- Python, o
toolchain do Lean 4 (via elan, mesma versão usada em desenvolvimento) e o
código do projeto -- reaproveitada por três serviços no
`docker-compose.yml`, espelhando os mesmos dois processos já geridos
localmente via PM2 (`ecosystem.config.cjs`):

| Serviço | O que roda | Porta |
|---|---|---|
| `webapp` | `gunicorn` servindo `backend.webapp.server:app` (produção -- troca o servidor de desenvolvimento do Flask, com `debug=True`, que não é adequado para isso) | 5000 |
| `lean-api` | `uvicorn app.main:app`, com `working_dir` em `backend/lean-api` | 8000 |
| `cli` | Não sobe com `docker compose up` (`profiles: ["cli"]`) -- só para comandos pontuais via `docker compose run` | — |

### Subindo

```bash
cp .env.example .env
# gere o hash da senha JA escapado para o .env (ver por que abaixo):
docker compose run --rm cli python3 backend/webapp/hash_password.py 'sua-senha' | sed 's/\$/$$/g'
# cole o resultado em LEAN4NET_ADMIN_PASSWORD_HASH no .env, preencha
# LEAN4NET_ADMIN_USER, LEAN4NET_SECRET_KEY e (opcional) ANTHROPIC_API_KEY

docker compose up -d --build
# webapp em http://localhost:5050, lean-api em http://localhost:8000
```

```bash
# CLI, pontualmente (sem subir nada persistente):
docker compose run --rm cli python3 main.py generate data/spec/example.yaml
docker compose run --rm cli python3 main.py verify data/build/example.lean
```

`./data` é montado como volume nos serviços `webapp` e `cli` -- specs e
`.lean` gerados/editados persistem no host, sobrevivem a
`docker compose down`.

### Por que escapar `$` no `.env`

O hash de senha (formato `scrypt` do werkzeug) sempre contém `$` --
e o Docker Compose interpreta `$` dentro de valores do `.env` como início
de variável, cortando/corrompendo o hash silenciosamente se não for
escapado como `$$`. O comando acima (com o `sed`) já devolve o hash
pronto para colar; se gerar de outra forma, escape manualmente antes de
colar no `.env`.

### Portas já em uso no host

Se `5000` ou `8000` já estiverem ocupadas por outra coisa na sua máquina,
mude só o lado esquerdo do mapeamento em `docker-compose.yml` (ex.:
`"5050:5000"`) -- o app dentro do container continua na porta original.

### Erros comuns

- **Container do `webapp` sobe e morre na hora**: uma das três variáveis
  (`LEAN4NET_ADMIN_USER`, `LEAN4NET_ADMIN_PASSWORD_HASH`,
  `LEAN4NET_SECRET_KEY`) está vazia -- `backend/webapp/auth.py` recusa
  subir sem elas (mensagem explicativa nos logs: `docker compose logs
  webapp`). O `docker-compose.yml` deliberadamente não bloqueia isso a
  nível do Compose (bloquearia até o próprio `cli` usado para gerar o
  hash), então o erro só aparece aqui.
- **`POST /api/generate` retorna 502 "falha de autenticação"**:
  `ANTHROPIC_API_KEY` não está definida (ou está vazia) no `.env`.
- **`POST /api/generate` retorna 502 "falha ao chamar a API da Anthropic:
  Connection error"** (mas o resto do app funciona): ver "Rede corporativa
  / proxy com interceptação de TLS" abaixo.

### Rede corporativa / proxy com interceptação de TLS

Se sua rede usa um proxy que intercepta TLS (Cisco Secure Access/Umbrella,
Zscaler, etc.), o container não confia na CA desse proxy por padrão --
`POST /api/generate` falha com `Connection error` / `CERTIFICATE_VERIFY_FAILED`
mesmo com `ANTHROPIC_API_KEY` certa (diagnóstico: `docker compose logs
webapp`, ou `docker compose exec webapp python3 -c "import ssl, socket;
ctx = ssl.create_default_context(); socket.create_connection(('api.anthropic.com',
443), timeout=5)"` -- se a conexão TCP funciona mas o handshake TLS falha
com `unable to get local issuer certificate`, é isso).

Correção: exportar a(s) CA(s) raiz do proxy do seu sistema e montá-la(s)
no container, atualizando o trust store antes de subir o servidor. Um
`docker-compose.override.yml` é carregado automaticamente pelo Compose
(sem precisar de `-f`) e não vai pro git -- copie de
`docker-compose.override.yml.example`:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
mkdir -p docker
# macOS -- exporte a CA raiz do Keychain (troque o nome pelo da sua rede):
security find-certificate -c "Nome da CA" -p /Library/Keychains/System.keychain \
  > docker/corporate-ca.crt
```

**Importante:** `update-ca-certificates` exige exatamente **um**
certificado por arquivo `.crt` -- se sua cadeia tiver mais de uma CA raiz,
exporte cada uma em um arquivo separado e monte todas (ver comentários no
`.example`). Depois:

```bash
docker compose up -d --force-recreate webapp
```

Os logs devem mostrar `N added, 0 removed; done` antes do gunicorn subir.

## Formato da especificação YAML

```yaml
vpcs:
  - name: main-vpc
    cidr: 10.0.0.0/16

subnets:
  - name: public-subnet
    vpc: main-vpc          # referencia o nome de uma VPC
    cidr: 10.0.1.0/24

security_groups:
  - name: web-sg
    ingress:
      - proto: tcp
        port_from: 80
        port_to: 80        # opcional; default = port_from
        src: 0.0.0.0/0     # CIDR de origem

instances:
  - name: web1
    subnet: public-subnet          # referencia o nome de uma subnet
    security_groups: [web-sg]      # referencia nomes de security groups
```

## Segurança e limitações da geração via LLM

Como o `.lean` é escrito pelo modelo a cada execução (não por um template
fixo), a corretude da ferramenta depende de duas camadas, não de uma só:

1. **O compilador Lean é a autoridade final.** Se o modelo traduzir mal os
   dados da spec ou a semântica de uma propriedade, o `theorem`
   correspondente falha a elaborar e aparece como FAIL — nunca como um PASS
   silenciosamente errado.
2. **Uma checagem defensiva em `backend/core/lean_codegen.py`
   (`_validate_statement_is_positive`) recusa um padrão específico de
   "trapaça"**: ao perceber que a especificação de fato viola uma
   propriedade, o modelo pode ser tentado a reescrever o enunciado do
   teorema como a *negação* dela (ex.: provar `algo = false` em vez de
   `algo = true`), o que produz uma prova trivial e disfarçaria uma
   violação real de segurança (porta sensível aberta ao mundo, por exemplo)
   como "verificado com sucesso". Essa checagem rejeita qualquer enunciado
   que mencione `false` ou não afirme `... = true`, independente do que o
   modelo decidiu fazer — é a defesa que importa de verdade, já que
   instruções no prompt (mesmo enfáticas) não são garantia. Isso já foi
   observado na prática durante o desenvolvimento; veja o comentário em
   `_validate_statement_is_positive` para o caso concreto. Como essa
   checagem vive dentro de `parse_properties()`, ela roda nas **duas**
   etapas — um `.lean` editado à mão com o mesmo truque é recusado pela
   etapa 2 antes de chegar ao compilador.

Outras limitações a ter em mente:

- **Cada geração chama a API da Anthropic** — custo e latência por execução
  da etapa 1 (não há cache entre chamadas), e o resultado depende da
  disponibilidade do serviço. A etapa 2 não chama o modelo, então
  reverificar o mesmo `.lean` é de graça.
- **Sem credenciais configuradas**, `generate()` levanta `LeanCodegenError`
  com uma mensagem explicativa (CLI: código de saída `2`; webapp: HTTP
  `502` com `{"error": "..."}`) em vez de deixar uma exceção crua escapar.
- O texto gerado **não é determinístico** entre execuções — o mesmo YAML
  pode produzir um `.lean` sintaticamente diferente (embora semanticamente
  equivalente) de uma chamada para outra.

## Extensão

Para adicionar uma nova propriedade: acrescente sua descrição em
`_required_properties()` e a especificação da semântica dela em
`_SYSTEM_PROMPT`, em `backend/core/lean_codegen.py` — o restante do pipeline
(chamada ao modelo, parsing dos marcadores, execução do Lean, parsing de
erro por linha) já é genérico. Lembre de manter o par de marcadores
(`-- @property:ID` / `-- @end_property:ID`) na instrução, é o que permite
ao `runner.py` religar um erro de compilação à propriedade certa — e de
formular o enunciado como uma proposição positiva (`... = true`), já que
`_validate_statement_is_positive` rejeita qualquer teorema que não siga
esse formato (ver seção acima).

Para infraestruturas maiores (centenas de subnets/regras), o `_SYSTEM_PROMPT`
já autoriza o modelo a usar `by native_decide` em vez de `by decide` quando
necessário — troca prova por avaliação no kernel por avaliação via código
nativo compilado, muito mais rápida, ao custo de confiar também no
compilador Lean para código nativo (não só no kernel).
