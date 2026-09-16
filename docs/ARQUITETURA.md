# lean4net — documentação de arquitetura

> Este documento consolida a visão de todo o monorepo. Para instruções rápidas
> de uso de cada componente, veja também [`README.md`](../README.md) (core) e
> [`backend/lean-api/README.md`](../backend/lean-api/README.md) (API de verificação Lean
> genérica), que permanecem como referência detalhada de cada um.

## 1. Visão geral

O `lean4net` explora uma ideia central: em vez de **testar** propriedades de
uma infraestrutura de nuvem (ou de qualquer sistema modelável formalmente),
usar o compilador do **Lean 4** para **provar** essas propriedades. Uma
especificação de infraestrutura em YAML é traduzida — por um **LLM da
Anthropic (Claude)**, a partir de um prompt que descreve a spec e a
semântica exata de cada propriedade — para um programa Lean onde cada
propriedade desejada vira um `theorem ... := by decide`: o kernel do Lean
avalia a proposição booleana correspondente sobre os dados exatos da
especificação e só aceita o arquivo (`exit code 0`) se ela reduzir para
`true`. Não há amostragem nem casos de teste — é verificação exaustiva sobre
os dados concretos da spec.

A geração do `.lean` deixou de ser determinística (templates Python) para
ser feita pelo modelo a cada execução; a garantia de corretude não vem mais
de "o código Python monta o arquivo certo", e sim de "o compilador Lean só
aceita o arquivo se as provas realmente fecharem" — se o modelo errar a
tradução dos dados ou a semântica de uma propriedade, o `theorem`
correspondente falha a elaborar e isso aparece como FAIL no relatório, não
como um falso positivo silencioso.

O repositório contém **três componentes** que compartilham esse mesmo
princípio, mas com escopos diferentes:

| Componente | Caminho | O que faz | Interface | Camada |
|---|---|---|---|---|
| **Core / CLI** | `backend/core/`, `main.py` | Pipeline principal em duas etapas: YAML de infra → Lean (`generate`), Lean → prova → relatório (`verify`) | linha de comando | backend (lib compartilhada) |
| **Webapp (backend)** | `backend/webapp/` | Servidor Flask sobre o mesmo pipeline do core, sem duplicar lógica | JSON sobre HTTP | backend |
| **Webapp (frontend)** | `frontend/` | HTML/CSS/JS estático (login + as 4 zonas, em 2 colunas), servido pelo Flask acima, sem framework nem build step | navegador | frontend |
| **lean-api** | `backend/lean-api/` | API REST genérica para verificar **qualquer** trecho de código Lean 4 (não específico a infraestrutura) | FastAPI | backend |

Não há nenhum componente de **banco de dados** no projeto — nenhuma das
três partes usa persistência além do sistema de arquivos (specs em
`data/spec/`, saída em `data/build/`), e o login do webapp (ver `backend/webapp/auth.py`)
guarda a única credencial em variável de ambiente, não em um banco.

O core e o webapp resolvem o mesmo problema (infraestrutura de nuvem) em duas
interfaces (CLI e web). O `lean-api` é um serviço independente e mais geral,
pensado para ser consumido por um agente de IA em um laço "gerar prova → ver
erro → corrigir → reenviar" — ele não sabe nada sobre VPCs, subnets ou
security groups.

```
              ┌───────────────────────────────────────────┐
              │           data/spec/*.yaml                 │
              │   (VPCs, subnets, security groups, ...)    │
              └────────────────────┬────────────────────────┘
                                    │
              ┌─────────────────────┴─────────────────────┐
              │        backend/core/  (pipeline core)       │
              │   spec_loader → lean_codegen → runner       │
              └─────────────────────┬─────────────────────┘
                                    │
            ┌───────────────────────┴────────────────────────┐
            │                                                 │
  ┌─────────┴──────────┐                        ┌─────────────┴──────────┐
  │    main.py (CLI)     │                        │  backend/webapp/server.py │
  │  relatório no          │                        │  (Flask, backend)        │
  │  terminal              │                        │            │             │
  └───────────────────────┘                        │            ▼             │
                                                     │        frontend/         │
                                                     │     (3 paineis web)      │
                                                     └─────────────────────────┘

  ── independente do diagrama acima ──────────────────────────────────

              ┌───────────────────────────────────────────┐
              │  backend/lean-api/ (FastAPI, standalone)    │
              │  POST /check { code } → { valid, errors }   │
              │  usa `lake env lean` via subprocesso        │
              └───────────────────────────────────────────┘
```

---

## 2. Core (`backend/core/` + `main.py`)

### 2.1 Pipeline (duas etapas)

O pipeline é cortado ao meio, no artefato `.lean`, em duas invocações
independentes: a etapa 1 usa o LLM e não o compilador; a etapa 2 usa o
compilador e não o LLM.

```
ETAPA 1 — `main.py generate data/spec/*.yaml`
data/spec/*.yaml  --[spec_loader.py]-->  InfraSpec (modelo Python, CIDRs parseados)
             --[lean_codegen.py]--> prompt -> Claude (claude-opus-5) -> data/build/*.lean
             --> grava o arquivo e o apresenta (stdout), com os intervalos
                 de linha de cada propriedade

ETAPA 2 — `main.py verify data/build/*.lean`
data/build/*.lean --[lean_codegen.parse_properties]--> intervalos de linha por propriedade
             --[runner.py]-------->  `lean data/build/*.lean` (compilador Lean 4)
             --> relatório PASS/FAIL por propriedade
```

**Por que separar.** O `.lean` é escrito por um modelo, e o valor da
ferramenta depende de alguém poder olhar para ele — "o compilador aceitou"
só significa algo se o enunciado provado for o enunciado pretendido. Cortar
o pipeline no artefato torna essa inspeção um passo de primeira classe, em
vez de um efeito colateral de um arquivo deixado em `data/build/`.

Consequências de design: **nenhum estado é compartilhado** entre as etapas
— o único elo é o arquivo, e a etapa 2 recalcula os intervalos de linha a
partir dos marcadores no texto atual (nunca reaproveita os da etapa 1, que
podem estar defasados se o arquivo foi editado). Daí `parse_properties()`
ter deixado de ser privada. Além disso, cada etapa tem seus próprios
pré-requisitos: só a 1 exige credenciais da Anthropic, só a 2 exige o
binário `lean`. Reverificar é grátis; regerar custa uma chamada de API.

### 2.2 Módulos

- **`backend/core/model.py`** — dataclasses imutáveis (`frozen=True`) que
  formam o "front-end" da especificação: `Vpc`, `Subnet`, `SecurityGroup`,
  `Rule`, `Instance`, `InfraSpec`. `Cidr` faz o parsing de uma string
  `"10.0.1.0/24"` para um endereço de rede inteiro de 32 bits (`addr`) mais o
  tamanho do prefixo (`plen`), via regex (`CIDR_RE`) e validação de faixa dos
  octetos (0–255) e do prefixo (0–32). `SpecError` é a exceção usada em toda
  a fase de carregamento/validação.

- **`backend/core/spec_loader.py`** — lê o YAML (`yaml.safe_load`) e faz
  validação **estrutural**, em Python, antes de gerar qualquer Lean:
  campos obrigatórios presentes (`_require`), CIDRs bem formados (delegado a
  `Cidr.parse`), nomes únicos dentro de cada categoria (`_check_unique`).
  Referências entre entidades (ex.: uma instância apontar para uma subnet que
  não existe) **não são validadas aqui** — isso é uma propriedade formal
  verificada pelo Lean (C4 por padrão, ver seção 2.4), de propósito: o
  objetivo é provar o máximo possível no Lean, não em Python.

- **`backend/core/lean_codegen.py`** — o gerador de código, agora via LLM.
  `generate(spec, sensitive_ports)`:
  1. Serializa a `InfraSpec` para JSON (CIDRs já como `addr`/`plen`
     inteiros, sem que o modelo precise reparsear texto) via `_user_prompt`.
  2. Chama `client.messages.create(...)` (`claude-opus-5`, thinking
     adaptativo, `output_config={"effort": "high"}`) com um `_SYSTEM_PROMPT`
     fixo que especifica: as `structure`s exigidas (`Cidr`, `Rule`, `Vpc`,
     `Subnet`, `SecGroup`, `Instance`), a semântica exata das funções de
     aritmética de CIDR (`netMask`, `netAddr`, `cidrContains`,
     `cidrOverlaps`, `coversPort`, `isOpenToWorld`) e o enunciado de cada
     uma das quatro propriedades exigidas — o modelo escreve o `.lean`
     inteiro (dados + funções auxiliares + teoremas `by decide`).
  3. Exige que o modelo envolva cada teorema com marcadores em comentário
     (`-- @property:P1` / `-- @end_property:P1`); `parse_properties`
     varre o texto **realmente devolvido** e calcula o intervalo de linhas
     de cada propriedade a partir da posição desses marcadores — nunca
     confiando em um número de linha que o próprio modelo alegue.
  4. Para cada propriedade encontrada, `_validate_statement_is_positive`
     inspeciona o texto entre o `theorem` e a tática (`by decide`/`by
     native_decide`) e **rejeita** o enunciado se ele mencionar `false` ou
     não afirmar `... = true`. Isso existe porque, na prática, o modelo já
     foi observado "trapaceando": ao perceber que a spec viola uma
     propriedade, ele reescreveu o enunciado como a negação dela (`algo =
     false`) para conseguir uma prova trivial em vez de deixar `decide`
     falhar — o que disfarçaria uma violação real de segurança como
     "verificado com sucesso". O `_SYSTEM_PROMPT` já proíbe isso
     explicitamente, mas essa checagem em Python é a defesa que realmente
     conta, por não depender do modelo obedecer a instrução. Como ela vive
     dentro de `parse_properties`, a etapa 2 a executa de novo: um `.lean`
     **editado à mão** com o mesmo truque é recusado antes de chegar ao
     compilador.
  5. Levanta `LeanCodegenError` se a chamada à API falhar por qualquer
     motivo — não só `anthropic.APIError` (autenticação, rate limit, rede),
     mas qualquer exceção (`except Exception` como rede de segurança final),
     porque o SDK levanta um `TypeError` puro (não um `APIError`) quando
     nenhuma credencial pode ser resolvida, e sem esse catch-all a exceção
     escaparia de `generate()` — no webapp isso quebrava o frontend (a
     página HTML de erro do Flask não é JSON válido). Também levanta
     `LeanCodegenError` se a resposta não tiver exatamente os quatro
     marcadores esperados, bem formados e não aninhados, ou se algum
     enunciado falhar a checagem do item 4.

  `build_prompt(spec, sensitive_ports) -> PromptPreview` monta a requisição
  (modelo, `max_tokens`, `thinking`, `output_config`, system e user prompt)
  **sem enviá-la**. `_call_llm` preenche a chamada a partir dessa mesma
  estrutura, em vez de repetir as constantes — é isso que garante que o
  prompt exibido pela interface (`POST /api/prompt`, antes da geração) seja
  literalmente o que vai para o modelo, sem risco de divergir com o tempo.

  Além de `generate()`, o módulo expõe duas funções usadas pela etapa 2,
  que só tem em mãos o arquivo `.lean`: `parse_properties(lean_source,
  sensitive_ports, custom_properties)` (os itens 3 e 4 acima, aplicados a
  um texto qualquer -- ver 2.4 para o que acontece quando `custom_properties`
  não é fornecido) e `extract_sensitive_ports(lean_source)`, que lê de volta
  o `def sensitivePorts : List Nat := [...]` do arquivo. Esta última existe
  para propriedades que precisem saber quais portas são "sensíveis" (a
  padrão, C3, é uma delas) sem que quem chama `verify` precise fornecer essa
  lista de novo -- lê-las do próprio arquivo verificado mantém o texto
  coerente mesmo quando a verificação acontece em outra sessão, com outros
  defaults, que a geração. Devolve `None` (e quem chama escolhe um default)
  se o `def` não existir ou não for uma lista de literais.

  O nome do teorema de cada propriedade (`Property.theorem_name`) vem de
  `CustomProperty.theorem_name` (`customProp_<id>`) quando a definição foi
  fornecida, ou é lido diretamente do bloco marcado no `.lean` quando não
  foi (ver 2.4) -- nunca mais um valor fixo em código. O intervalo de linhas
  continua sendo devolvido como `list[Property]`, exatamente como antes, o
  que é o que permite ao `runner.py` religar um erro de compilação de volta
  à propriedade correspondente sem exigir que o Lean saiba nada sobre
  "propriedades".

- **`backend/core/runner.py`** — `find_lean_binary(explicit)` resolve o binário
  `lean` via `shutil.which` (ou um caminho explícito passado por
  `--lean-bin`), levantando `FileNotFoundError` com uma mensagem acionável se
  não encontrar. `run_lean(lean_file, properties, lean_bin)` roda `lean
  <arquivo>` uma única vez (`subprocess.run`, timeout de 300s) sobre o
  arquivo inteiro — como cada propriedade é um `theorem` independente, uma
  falha em `decide` numa propriedade não impede o Lean de continuar
  elaborando as seguintes, então um único processo já reporta todos os
  erros de uma vez. A saída é parseada com uma regex
  (`arquivo:linha:coluna: error: mensagem`), agregando linhas de continuação
  à mensagem anterior, e cada erro é atribuído à propriedade cujo intervalo
  de linhas o contém. Erros fora de qualquer intervalo conhecido (ex.: erro
  de sintaxe no prelúdio) viram entradas de propriedade `"?"` e sempre
  derrubam o veredito geral (`overall_ok`), mesmo que todas as propriedades
  "de verdade" pareçam ok.

- **`main.py`** — CLI (`argparse` com subcomandos), uma função por etapa:

  - `generate <spec.yaml> [--out] [--sensitive-ports]` → `cmd_generate`:
    carrega e valida a spec, chama `generate()`, escreve o `.lean` em
    `data/build/<nome-da-spec>.lean` (ou em `--out`) e o apresenta. O **código
    Lean vai para stdout e as mensagens de progresso para stderr** (helper
    `_info`), de modo que `main.py generate spec.yaml > infra.lean` produz
    um arquivo válido. Saída: `0` gerou, `2` erro de spec/credenciais/
    formato da resposta.
  - `verify <arquivo.lean> [--lean-bin]` → `cmd_verify`: resolve o binário
    Lean, lê o arquivo, recupera as propriedades com `parse_properties()` e
    as portas com `extract_sensitive_ports()`, roda `run_lean()` sobre o
    arquivo **em disco** (para que os números de linha do compilador
    correspondam ao que o usuário vê) e imprime o relatório alinhado por
    propriedade (`PASS`/`FAIL`, ✓/✗, mensagens de erro indentadas). Saída:
    `0` tudo provado, `1` alguma propriedade falhou, `2` erro de
    configuração (Lean ausente, arquivo ilegível ou sem os marcadores).

  Note que `verify` **não** aceita `--sensitive-ports`: as portas só
  influenciam a geração e o texto do relatório, e a fonte de verdade para o
  relatório é o arquivo que está sendo verificado.

### 2.3 Formato da especificação YAML

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

Três specs de exemplo ilustram o comportamento do verificador (`data/spec/`):

| Arquivo | Propósito | Resultado esperado |
|---|---|---|
| `example.yaml` | infraestrutura consistente (VPC + subnet pública/privada, web + db) | todas as 4 propriedades passam |
| `example_bad.yaml` | múltiplas violações propositais (subnet fora da VPC, subnets sobrepostas, SSH aberto ao mundo, referência a security group inexistente) | P1, P2, P3 e P4 falham |
| `example_partial.yaml` | igual a `example.yaml`, exceto que `ssh-sg` libera a porta 22 para `0.0.0.0/0` | só P3 falha — demonstra que o veredito é **por propriedade**, não tudo-ou-nada |

### 2.4 Propriedades customizadas

Não há mais um conjunto fixo de propriedades embutido no prompt: **todas**
— inclusive as quatro originais deste projeto — vêm de uma lista de
`CustomProperty` (id + descrição em linguagem natural) fornecida por quem
chama `generate`/`verify`, sem persistência (o projeto não tem banco de
dados). `DEFAULT_PROPERTIES` (`backend/core/lean_codegen.py`) e só o texto
usado para pré-popular essa lista quando ninguém fornece nada — não é
tratado de forma especial pelo gerador nem pelo parser:

| ID padrão | Descrição pré-populada |
|----|-----|
| C1 | Toda subnet está contida dentro do CIDR da sua própria VPC. |
| C2 | Nenhum par de subnets da mesma VPC tem faixas de IP sobrepostas. |
| C3 | Nenhuma regra de ingress libera uma porta sensível (22, 3389 por padrão) para `0.0.0.0/0`. |
| C4 | Toda instância referencia uma subnet e security groups que de fato existem na spec. |

Cada propriedade é um `theorem` Lean independente provado por `decide`, então
o veredito é reportado propriedade a propriedade — uma falha numa não afeta
o resultado das demais.

Fornecida via `--properties <arquivo.json>` da CLI ou do editor da webapp
(`frontend/`, seção 3.2):

- **`CustomProperty`** (`backend/core/model.py`) — `id` (formato `C<número>`,
  atribuído por quem chama, nunca digitado por um usuário final na webapp —
  o editor gera sequencialmente) e `description` (texto livre, editável a
  qualquer momento). O `theorem_name` é derivado deterministicamente:
  `f"customProp_{id}"` — nunca escolhido por quem chama, exatamente para
  que a etapa de verificação consiga reconstruir o nome esperado do
  teorema mesmo sem ter a definição original em mãos.
- **`load_custom_properties()`** (`backend/core/spec_loader.py`) — validação
  estrutural (id no formato certo, sem duplicatas, descrição não vazia),
  espelhando `load_spec()` para a spec YAML.
- **Prompt** (`backend/core/lean_codegen.py`) — o `_SYSTEM_PROMPT` é uma
  string **fixa** que nunca muda entre chamadas: descreve as `structure`s e
  funções auxiliares disponíveis, e uma seção genérica ("para cada
  propriedade listada na mensagem do usuário: formule um enunciado Lean
  preciso, declare um `theorem` com o nome EXATO indicado, prove por
  `decide`/`native_decide`, envolva com o par de marcadores
  `-- @property:<id>` / `-- @end_property:<id>`, na ordem listada") mais a
  regra crítica de sempre formular a proposição positiva, nunca reescrever
  como negação. É o *user prompt* que muda a cada chamada, listando
  `id`/nome do teorema/descrição de cada propriedade recebida.
- **`parse_properties()`** exige exatamente as propriedades passadas em
  `custom_properties` (erro se alguma estiver faltando no `.lean`), mas
  também aceita — sem exigir — qualquer OUTRO marcador com id no formato
  `C<número>` (ou o legado `P<número>`, de arquivos gerados antes desta
  unificação): o nome do teorema, nesse caso, é lido diretamente do bloco
  (`theorem <nome> :`) em vez de adivinhado pela convenção, e a descrição
  aparece como `"(propriedade customizada -- descrição original não
  fornecida nesta verificação)"`. Isso cobre `verify` de um `.lean` salvo
  em disco sem reenviar a lista de propriedades do editor.
- **`generate()`** recusa gerar (`LeanCodegenError`) se `custom_properties`
  vier vazio — não há o que provar.

### 2.5 Uso

Pré-requisitos (um por etapa):
- **Etapa 2**: Lean 4 instalado via [elan](https://github.com/leanprover/elan)
  (`lean --version` funcionando). Nenhuma dependência de Mathlib é
  necessária — só `Init`, que já vem com o toolchain.
- **Etapa 1**: credenciais da Anthropic configuradas (`ANTHROPIC_API_KEY` no
  ambiente, ou um login prévio via `ant auth login`) — necessárias para
  `lean_codegen.py` chamar a API e gerar o `.lean`.

```bash
pip install -r requirements.txt   # pyyaml + anthropic

# etapa 1: YAML -> Lean
python3 main.py generate data/spec/example.yaml
python3 main.py generate data/spec/example.yaml \
  --out data/build/minha_infra.lean \
  --sensitive-ports 22,3389,3306

# etapa 2: Lean -> compilador
python3 main.py verify data/build/example.lean            # tudo passa
python3 main.py verify data/build/example_bad.lean        # tudo falha (violações propositais)
python3 main.py verify data/build/example_partial.lean    # só a P3 falha (SSH aberto ao mundo)
python3 main.py verify data/build/example.lean --lean-bin /caminho/para/lean
```

O `.lean` gerado fica em `data/build/` e pode ser aberto no VS Code com a extensão
do Lean 4 para inspecionar os teoremas provados interativamente — entre as
duas etapas, que é exatamente o ponto da separação.

### 2.6 Extensão

Adicionar uma propriedade nova é so incluir mais um item na lista de
`CustomProperty` passada a `generate()`/`build_prompt()` — via
`--properties` na CLI ou o editor na webapp (ver 2.4) — não é mais preciso
editar `lean_codegen.py` para isso: o pipeline (prompt, parsing dos
marcadores, execução do Lean, parsing de erro por linha) já é genérico. O
enunciado descrito deve sempre ser formulável como uma proposição positiva
(`... = true`), já que `_validate_statement_is_positive` rejeita qualquer
teorema que não siga esse formato (ver 2.2, item 4) — isso é responsabilidade
de quem escreve a descrição em linguagem natural, não algo que se configure
em código.

Editar `_SYSTEM_PROMPT` (em `lean_codegen.py`) só é necessário para mudar
algo estrutural que vale para *todas* as propriedades — por exemplo, expor
uma nova `structure`/função auxiliar (seção 2 do prompt) que descrições
futuras possam referenciar.

Para infraestruturas maiores (centenas de subnets/regras), o
`_SYSTEM_PROMPT` já autoriza o modelo a preferir `by native_decide` a `by
decide` quando necessário — troca "prova avaliada pelo kernel" por
"avaliação via código nativo compilado" — muito mais rápida, ao custo de
confiar também no compilador Lean para código nativo (e não apenas no
kernel).

---

## 3. Webapp (`backend/webapp/` + `frontend/`)

Interface web sobre o **mesmo pipeline** do core (`spec_loader` →
`lean_codegen` → `runner`) — o servidor Flask não duplica nenhuma lógica de
domínio, apenas adapta o pipeline de CLI para HTTP. As duas etapas da CLI
viram dois endpoints, e o papel de "arquivo `.lean` no disco" (o elo entre
elas) é assumido pelo painel de código no navegador.

O backend (`backend/webapp/`) e o frontend (`frontend/`) vivem em árvores
separadas — `backend/webapp/server.py` serve os arquivos estáticos de
`frontend/` (na raiz do projeto, dois níveis acima) via
`static_folder="../../frontend"` — a separação física deixa claro que
`backend/webapp/` só tem código Python (servidor + login) e `frontend/`
só tem HTML/CSS/JS puro, sem misturar os dois numa mesma árvore.

### 3.1 Backend — `backend/webapp/server.py`

Flask app com três endpoints JSON e um static file server:

| Rota | Método | Descrição |
|---|---|---|
| `/` | GET | Serve `frontend/index.html`. |
| `/api/example` | GET | Devolve o conteúdo de `data/spec/example.yaml` (usado para popular o editor ao carregar a página). |
| `/api/prompt` | POST | **Prévia da etapa 1.** Mesmo corpo de `/api/generate` → `{ "model", "max_tokens", "thinking", "output_config", "system", "user" }`. Monta o prompt e **não o envia** — não chama o LLM nem o compilador. |
| `/api/generate` | POST | **Etapa 1.** Corpo `{ "yaml": "...", "sensitive_ports": "22,3389", "custom_properties": [...] }` → `{ "lean_source": "...", "properties": [...] }`. Chama o LLM, não o compilador. |
| `/api/verify` | POST | **Etapa 2.** Corpo `{ "lean_source": "...", "custom_properties": [...] }` → `{ "report": {...} }`. Chama o compilador, não o LLM. |

Detalhes de implementação:
- `/api/prompt` e `/api/generate` recebem o mesmo corpo e compartilham a
  validação em `_spec_from_request()` (YAML não vazio, portas parseáveis,
  `load_spec`), que devolve `(spec, portas)` ou uma resposta de erro pronta.
  Efeito colateral útil: como o frontend chama `/api/prompt` primeiro, um
  YAML inválido é recusado **antes** de qualquer chamada à API paga.
- Os endpoints são **sem estado**: o servidor não guarda o Lean gerado
  entre as etapas. O que a etapa 2 verifica é o texto que o cliente enviou —
  possivelmente editado à mão no navegador — e as propriedades são
  recalculadas sobre esse texto com `parse_properties()`. Não há como o
  relatório se referir a uma versão do código diferente da que o usuário
  está vendo.
- O binário `lean` é resolvido uma única vez e cacheado em `_lean_bin_cache`
  (`_get_lean_bin()`), evitando `shutil.which` a cada requisição. Só
  `/api/verify` precisa dele — gerar funciona em uma máquina sem Lean.
- Cada requisição escreve o YAML recebido (etapa 1) ou o `.lean` recebido
  (etapa 2) em um diretório temporário exclusivo
  (`tempfile.TemporaryDirectory`), removido ao final.
- Erros de especificação (`SpecError`), YAML malformado, portas inválidas e
  código Lean sem os marcadores esperados retornam **HTTP 400** com uma
  mensagem; falha ao gerar o Lean via LLM (`LeanCodegenError` — API
  indisponível, sem credenciais, resposta malformada ou enunciado recusado
  pela checagem anti-trapaça) retorna **HTTP 502**; falha ao resolver o
  binário Lean ou ao executá-lo retorna **HTTP 500**.
- `report` no JSON de resposta espelha `VerificationReport` de
  `backend/core/runner.py` (`success`, `results[]` com `id`/`theorem_name`/
  `description`/`ok`/`messages`, `raw_stdout`, `raw_stderr`, `returncode`).

### 3.2 Frontend — `frontend/`

Três arquivos estáticos, sem framework nem build step:

- **`index.html`** — três painéis (`.pane`) num grid, numerados conforme as
  etapas: **1. YAML de entrada** (textarea editável + upload de arquivo +
  editor de propriedades customizadas + botão "Gerar Lean" -- as portas
  sensíveis não têm mais campo próprio na UI, o servidor usa o default
  `22,3389`, ver 2.4), **2.** o painel do meio,
  com duas abas (`.view-tabs`, que fazem o papel do `<h2>` dos outros
  painéis) — **Prompt** (um `<pre>` somente leitura) e **Lean 4 gerado** (um
  textarea **editável**) — mais o badge de status da geração e o botão
  "Verificar", e **3. Resultado da verificação** (com um badge de status
  geral).
- **`app.js`** — sem dependências externas. Principais responsabilidades:
  - `loadExample()` busca `/api/example` ao carregar a página para
    pré-popular o editor.
  - Upload de arquivo local via `FileReader` (`fileInput`).
  - Editor de propriedades customizadas (`.properties-editor`, colapsável):
    `customProperties` é um array em memória (`{id, description}`, sem
    persistência), com `id` gerado sequencialmente (`C1`, `C2`, ...) a cada
    "+ Adicionar" -- nunca digitado pelo usuário. `renderCustomProperties()`
    redesenha a lista e o contador no rótulo do toggle; `custom_properties`
    (o array inteiro) vai junto no corpo de `/api/prompt`, `/api/generate` e
    `/api/verify` -- ver 2.4.
  - `runGenerate()` faz **duas** requisições em sequência: `POST /api/prompt`
    (instantânea) para mostrar o prompt na aba "Prompt" e, só então,
    `POST /api/generate`, trocando para a aba "Lean 4 gerado" quando o código
    chega. O prompt fica visível durante toda a espera da geração — que é o
    ponto: ver o que foi pedido ao modelo antes de julgar o que ele
    devolveu. `formatPrompt()` monta o texto exibido (parâmetros da
    requisição + system + user).
  - `runVerify()` faz `POST /api/verify` **com o conteúdo atual do textarea**
    (e força a aba "Lean 4", para que o relatório e o código a que ele se
    refere estejam à vista); `renderResults()` desenha a lista de
    propriedades (ícone ✓/✗, id, nome do teorema, descrição, mensagens de
    erro), atualizando os badges (`idle`/`running`/`pass`/`fail`/`error`).
    Todo texto vindo do servidor é escapado (`escapeHtml`) antes de ir para
    `innerHTML`.
  - `syncVerifyEnabled()` mantém o botão "Verificar" desabilitado enquanto
    não houver Lean no painel — inclusive se o usuário apagar o conteúdo
    depois de gerar (daí o listener de `input`, em vez de habilitar o botão
    uma vez só ao fim da etapa 1).
  - Painéis redimensionáveis por arraste (`makeResizer`, Pointer Events),
    com duplo clique para restaurar o tamanho padrão, e um layout mobile
    (`MOBILE_QUERY`, `max-width: 800px`) que desativa o redimensionamento e
    empilha os painéis.
- **`style.css`** — tema visual dos painéis, badges de status e das listas
  de propriedades.

### 3.3 Uso

```bash
pip install -r requirements.txt   # inclui flask, além de pyyaml
python3 backend/webapp/server.py
# abre http://127.0.0.1:5000
```

Porta configurável via variável de ambiente `PORT` (default `5000`); o
servidor roda com `debug=True`, adequado para uso local/desenvolvimento, não
para exposição pública.

---

## 4. lean-api (`backend/lean-api/`)

API REST **independente** do core/webapp — não conhece VPCs, subnets nem
nenhum conceito de infraestrutura. Recebe qualquer trecho de código Lean 4 e
devolve um relatório estruturado (válido ou não, erros e warnings com
linha/coluna), pensada para ser consumida por um agente de IA num laço
"gerar prova → ver erro → corrigir → reenviar".

### 4.1 Arquitetura interna

```
backend/lean-api/
├── app/
│   ├── main.py         # rotas HTTP (FastAPI): status codes, validação, logging de acesso
│   ├── models.py        # modelos Pydantic de request/response
│   └── lean_runner.py   # execução do Lean, parsing de diagnósticos, timeout, exceções
└── tests/test_api.py    # pytest + FastAPI TestClient (10 testes, contra o Lean real)
```

Separação estrita de camadas: `app/main.py` **nunca** chama `subprocess`
diretamente — depende apenas do protocolo `LeanExecutor`:

```python
class LeanExecutor(Protocol):
    def check(self, code: str) -> CheckResponse: ...
    def health(self) -> HealthResponse: ...
```

A única implementação hoje é `SubprocessLeanExecutor`
(`app/lean_runner.py`), injetada via `Depends(get_lean_executor)`. Isso deixa
o caminho aberto para, no futuro, trocar por um `LspLeanExecutor` que mantém
um processo persistente do Lean Language Server (ver seção 4.6) sem tocar em
`main.py` ou `models.py`.

`SubprocessLeanExecutor.check()`:
1. valida que o comando configurado (`LEAN_COMMAND`, default `lake env
   lean`) existe;
2. grava o código recebido em `Main.lean`, dentro de um diretório temporário
   exclusivo;
3. executa `<LEAN_COMMAND> <arquivo>` com `cwd=LEAN_PROJECT_DIR`, argumentos
   em lista (nunca `shell=True`), `start_new_session=True` e timeout — se
   estourar, mata o **grupo de processos inteiro** via `os.killpg` (não só o
   processo imediato, importante porque `lake env lean` pode ter filhos);
4. sanitiza a saída (substitui o caminho absoluto do arquivo temporário por
   `Main.lean`, remove o diretório temporário) e faz o parsing dos
   diagnósticos;
5. remove o diretório temporário, sempre (`finally`).

`health()` roda `lake --version` e `<LEAN_COMMAND> --version` como sondas
independentes (timeout curto, `_VERSION_PROBE_TIMEOUT = 5s`), sem afetar o
estado da API.

### 4.2 Parsing de diagnósticos

`parse_diagnostics()` reconhece o formato `arquivo:linha:coluna:
severidade: mensagem` (inclusive a variante `error(código):`), agregando
linhas de continuação à mensagem anterior até a próxima linha reconhecida ou
o fim da saída. É **best-effort**: mensagens fora desse formato não viram
entradas estruturadas em `errors`/`warnings`, mas `stdout`/`stderr` brutos
sempre são preservados na resposta, então nenhuma informação é perdida.

### 4.3 Modelos (`app/models.py`)

- `CheckRequest { code: str }`
- `Diagnostic { line?, column?, severity: "error"|"warning", message }`
- `CheckResponse { valid, exit_code, errors[], warnings[], stdout, stderr }`
- `HealthResponse { status: "ok"|"unavailable", lean_available, lean_version?, lake_available, lake_version?, detail? }`

`valid` reflete **apenas** o exit code do processo Lean — `True` mesmo com
warnings presentes. Erros de compilação ou provas inválidas resultam em
`valid: false` com **HTTP 200** (a requisição em si foi bem-sucedida; é o
código Lean que é inválido).

### 4.4 Endpoints

| Rota | Método | Descrição |
|---|---|---|
| `/health` | GET | Reporta disponibilidade do Lean/Lake configurados. Sempre HTTP 200. |
| `/check` | POST | Recebe `{ "code": "..." }`, devolve o `CheckResponse`. |
| `/docs` | GET | Swagger UI (gerado automaticamente pelo FastAPI). |

### 4.5 Códigos de status HTTP

| Código | Quando |
|--------|--------|
| `200`  | O Lean rodou normalmente — inclusive com erros de compilação/prova inválida (`valid: false`). |
| `413`  | `code` excede `LEAN_MAX_CODE_SIZE` caracteres. |
| `422`  | Corpo malformado ou sem o campo `code` (validação padrão FastAPI/Pydantic). |
| `503`  | O comando Lean/Lake configurado não pôde ser executado. |
| `504`  | Execução excedeu `LEAN_TIMEOUT` segundos (não 408 — a requisição chegou completa; é o processo upstream, o Lean, que não respondeu a tempo, o mesmo papel semântico de um gateway timeout). |
| `500`  | Falha inesperada na própria API. |

### 4.6 Configuração (variáveis de ambiente)

| Variável | Padrão | Descrição |
|---|---|---|
| `LEAN_PROJECT_DIR` | `os.getcwd()` | `cwd` usado ao rodar o comando Lean (de onde `lake env` resolve o projeto/dependências). |
| `LEAN_COMMAND` | `lake env lean` | Comando usado para verificar um arquivo, tokenizado via `shlex.split` e executado sem shell. |
| `LEAN_TIMEOUT` | `10` | Timeout em segundos por requisição. |
| `LEAN_MAX_CODE_SIZE` | `100000` | Tamanho máximo (caracteres) aceito em `code`. |

Nenhum caminho absoluto é fixado no código-fonte.

### 4.7 Segurança — leitura obrigatória antes de expor a API

**Esta API não é um sandbox forte.** O código Lean enviado roda como um
processo do SO com os mesmos privilégios do processo da API. O Lean tem
metaprogramação e `#eval`/`IO` arbitrário — nada aqui impede código
malicioso de tentar acessar filesystem, rede etc. com as permissões do
usuário que roda a API, ainda que o subprocesso seja lançado sem shell, com
timeout e argumentos em lista.

**Use apenas localmente ou em ambiente controlado e isolado** (container
descartável, VM, usuário sem privilégios) — nunca exposta diretamente à
internet ou a código de origem não confiável em produção.

A API também nunca loga o conteúdo do código Lean enviado — apenas
metadados (tamanho em caracteres) — para não poluir logs com código grande
ou sensível.

### 4.8 Instalação e execução

```bash
cd backend/lean-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# API em http://127.0.0.1:8000, docs em http://127.0.0.1:8000/docs
```

Pré-requisitos: Python 3.10+, Lean 4 + Lake via
[elan](https://github.com/leanprover/elan). Não requer Mathlib — os exemplos
usam apenas `Init` (`Nat` em vez de `ℕ`; a notação Unicode de Mathlib só
funciona se `LEAN_PROJECT_DIR` apontar para um projeto Lake com Mathlib como
dependência).

### 4.9 Testes

```bash
cd backend/lean-api
pytest tests/ -v
```

10 testes de integração via `TestClient` do FastAPI, contra a aplicação
real — **exigem Lean/Lake no PATH**. Os cenários de "Lean indisponível" e
"timeout" trocam a implementação de `LeanExecutor` via
`app.dependency_overrides` (comando inexistente / timeout bem curto), sem
desinstalar nada do sistema. Cobrem: `/health` (disponível e indisponível),
prova válida por `decide` e por `induction`, código vazio, prova inválida
(inclui checar que o caminho do arquivo temporário não vaza na resposta),
campo `code` ausente (422), código grande demais (413), timeout (504) e
Lean indisponível (503).

### 4.10 Limitações conhecidas

- Sem sandbox real de execução (ver 4.7).
- Um processo Lean novo por requisição — sem reuso de estado, custo de
  start-up perceptível (~1–4s típico) mesmo para arquivos triviais.
- Parsing de diagnósticos é best-effort (formatos não reconhecidos ainda
  aparecem em `stdout`/`stderr` brutos, só não viram `errors`/`warnings`
  estruturados).
- Sem fila/limite de concorrência: requisições simultâneas disparam
  processos Lean em paralelo, sem controle de CPU/memória além do timeout
  por requisição.
- `ℕ` (notação Mathlib) não funciona sem um `LEAN_PROJECT_DIR` que tenha
  Mathlib como dependência.

### 4.11 Evolução futura: LSP/RPC

A implementação atual satisfaz o protocolo `LeanExecutor` justamente para
permitir, no futuro, um `LspLeanExecutor` que mantenha um **processo
persistente do Lean Language Server**, falando LSP/RPC
(`textDocument/didOpen`, `textDocument/publishDiagnostics`, etc.) em vez de
iniciar um processo novo por requisição. Isso habilitaria reaproveitar o
processo entre requisições (eliminando o custo de start-up), diagnósticos
incrementais, estado de arquivos abertos entre chamadas, e recursos como
`hover`/`completion`/inspeção de *proof states* — relevante para um agente
de IA que gera e corrige provas iterativamente. A troca seria só
`get_lean_executor()` retornar o novo executor.

---

## 5. Relação entre os componentes e pontos de atenção

- **Core e webapp compartilham 100% da lógica de domínio** (`backend/core/`);
  o webapp é estritamente uma camada de adaptação HTTP + UI sobre o mesmo
  pipeline usado por `main.py`. Uma mudança em `lean_codegen.py` (ex.: nova
  propriedade) aparece automaticamente em ambas as interfaces. Isso vale
  também para o corte em duas etapas: `generate`/`verify` na CLI e
  `/api/generate` + `/api/verify` no webapp são a mesma fronteira, com o
  mesmo contrato (o texto Lean é o único elo) — só muda quem guarda o
  artefato entre as etapas, o disco ou o navegador.
- **`lean-api` é desacoplada de propósito** — não importa nada de
  `backend/core/`. Ela resolve um problema mais geral (verificar Lean
  arbitrário) e não deveria ganhar conhecimento de infraestrutura de nuvem;
  se o core algum dia precisar de um backend de verificação mais rico (ex.:
  reuso de processo Lean), o caminho natural é fazer `backend/core/runner.py`
  falar com uma API no estilo de `lean-api`, não o inverso.
- **Nenhum dos três componentes tem autenticação** — todos assumem uso
  local/confiável. Antes de expor qualquer um deles além de `localhost`,
  isso precisa ser adicionado explicitamente (e, no caso da `lean-api`, a
  ausência de sandbox de execução é o bloqueador mais crítico, ver 4.7).
- **Testes automatizados existem apenas para `lean-api`**
  (`backend/lean-api/tests/test_api.py`). O core e o webapp são verificados
  manualmente através das specs de exemplo (`example.yaml`,
  `example_bad.yaml`, `example_partial.yaml`), que cobrem os casos "tudo
  passa", "tudo falha" e "falha parcial" — mas não há suíte de testes
  automatizada para `backend/core/` nem para `backend/webapp/server.py`.
- **A geração do `.lean` via LLM (`lean_codegen.py`) desloca onde a
  corretude é garantida.** Antes, era pelo código Python que montava o
  template; agora é pelo compilador Lean (autoridade final, sempre) mais
  uma checagem em Python que recusa um padrão específico de resposta
  malformada — o modelo reformular o enunciado de um teorema como a
  negação da propriedade para evitar uma falha de compilação legítima
  quando a spec de fato viola a propriedade (ver 2.2, item 4). Esse caso
  foi observado na prática, não é hipotético. Qualquer nova propriedade
  adicionada ao gerador deve manter esse mesmo cuidado: o enunciado no
  prompt tem que ser fixo e positivo, e a validação em Python (não só a
  instrução no prompt) é o que impede uma resposta do modelo de disfarçar
  uma violação real como "verificado com sucesso".
