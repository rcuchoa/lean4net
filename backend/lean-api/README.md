# lean-api

API REST local, em Python (FastAPI), para verificar código Lean 4. Um
agente de IA (ou qualquer cliente HTTP) envia um trecho de código Lean e
recebe de volta um JSON estruturado dizendo se o código é válido, com os
erros e warnings produzidos pelo compilador — para permitir um laço de
"gerar prova → ver erro → corrigir → reenviar".

A primeira versão executa o Lean via subprocesso (`lake env lean`), uma
vez por requisição. A arquitetura já separa claramente a camada HTTP
(`app/main.py`) do mecanismo de verificação (`app/lean_runner.py`) para
que esse mecanismo possa, depois, ser trocado por um processo
persistente do Lean Language Server — ver [Evolução futura](#evolução-futura-lsprpc).

## ⚠️ Aviso de segurança

**Esta API não é um sandbox de segurança forte.** O código Lean enviado
é executado como um processo do sistema operacional (`lake env lean`)
com os mesmos privilégios do processo da API. Embora o subprocesso seja
lançado sem shell, com argumentos em lista, timeout e sem herdar um
diretório de trabalho gravável fora do necessário, o Lean é uma
linguagem com metaprogramação e `#eval`/`IO` arbitrário — nada aqui
impede código malicioso de tentar acessar o sistema de arquivos, rede
etc. com as permissões do usuário que roda a API.

Use esta primeira versão apenas **localmente ou em um ambiente
controlado e isolado** (container descartável, VM, usuário sem
privilégios) — nunca exposta diretamente à internet ou a código Lean de
origem não confiável em produção.

## Estrutura do projeto

```
lean-api/
├── app/
│   ├── __init__.py
│   ├── main.py          # rotas HTTP (FastAPI) — status codes, validação de request
│   ├── models.py         # modelos Pydantic de request/response
│   └── lean_runner.py    # execução do Lean, parsing de erros/warnings, timeout, exceções
├── tests/
│   └── test_api.py       # pytest + FastAPI TestClient
├── requirements.txt
├── .gitignore
└── README.md
```

## Pré-requisitos

- **Python 3.10+**
- **Lean 4 e Lake**, instalados via [elan](https://github.com/leanprover/elan)
  (o instalador/gerenciador de versões oficial do Lean):

  ```bash
  curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh
  # siga as instruções do instalador e reabra o shell (ou source no seu rc file)
  lean --version
  lake --version
  ```

Esta API **não requer Mathlib** — os exemplos usados (teoremas sobre
`Nat` com `decide`/`induction`) usam apenas `Init`, que já vem com o
toolchain padrão do Lean. Isso mantém a instalação rápida.

### Diretório do projeto Lean

A API roda `lake env <comando>` a partir de um diretório
(`LEAN_PROJECT_DIR`) para herdar o ambiente (toolchain, `LEAN_PATH`,
dependências) desse projeto Lean/Lake. Duas opções:

1. **Sem projeto Lake dedicado (mais simples):** não defina
   `LEAN_PROJECT_DIR`. O fallback é o diretório de trabalho atual; se
   não houver um `lakefile` em nenhum ancestral desse diretório,
   `lake env` ainda funciona, usando apenas o toolchain padrão do elan.
   Suficiente para verificar código que só usa `Init`.

2. **Com um projeto Lake dedicado (recomendado se for usar
   dependências, como Mathlib, no futuro):**

   ```bash
   mkdir meu_projeto_lean && cd meu_projeto_lean
   lake init meu_projeto_lean
   lake build   # baixa toolchain/dependências e compila uma vez
   ```

   Depois aponte `LEAN_PROJECT_DIR` para esse diretório (veja
   [Variáveis de ambiente](#variáveis-de-ambiente)).

## Instalação (Python)

```bash
cd backend/lean-api
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Variáveis de ambiente

| Variável              | Padrão              | Descrição |
|------------------------|----------------------|-----------|
| `LEAN_PROJECT_DIR`     | diretório atual (`os.getcwd()`) | Diretório usado como `cwd` ao rodar o comando Lean (de onde `lake env` resolve o projeto). |
| `LEAN_COMMAND`         | `lake env lean`     | Comando usado para verificar um arquivo `.lean`, tokenizado e executado sem shell (`argv[0] argv[1] ... <arquivo>`). |
| `LEAN_TIMEOUT`         | `10`                 | Timeout, em segundos, para a execução do Lean por requisição. |
| `LEAN_MAX_CODE_SIZE`   | `100000`             | Tamanho máximo (em caracteres) aceito no campo `code` de `POST /check`. |

Exemplo:

```bash
export LEAN_PROJECT_DIR=/caminho/para/meu_projeto_lean
export LEAN_TIMEOUT=15
export LEAN_MAX_CODE_SIZE=50000
export LEAN_COMMAND="lake env lean"
```

Nenhum caminho absoluto é fixado no código — tudo vem dessas variáveis,
com fallback para o diretório de trabalho atual.

## Executando a API

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

A API sobe em `http://127.0.0.1:8000`. Documentação interativa
(Swagger UI) disponível em `http://127.0.0.1:8000/docs`.

## Executando os testes

```bash
cd backend/lean-api
pytest tests/ -v
```

Os testes usam o `TestClient` do FastAPI contra a aplicação real e
**exigem Lean/Lake disponíveis no PATH** — os mesmos usados para rodar
a própria API — mas não dependem de nenhuma outra instalação externa.
Os cenários de "Lean indisponível" e "timeout" não desinstalam nada do
sistema: eles trocam a implementação de `LeanExecutor` usada pela API
(via `app.dependency_overrides`) por uma configurada com um comando
inexistente ou um timeout bem curto.

Resultado esperado (10 testes, ambiente local):

```
tests/test_api.py::test_health_reports_lean_available PASSED
tests/test_api.py::test_health_when_lean_unavailable PASSED
tests/test_api.py::test_check_valid_decide_proof PASSED
tests/test_api.py::test_check_valid_induction_proof PASSED
tests/test_api.py::test_check_empty_code_is_trivially_valid PASSED
tests/test_api.py::test_check_invalid_proof_returns_200_with_valid_false PASSED
tests/test_api.py::test_check_missing_code_field_returns_422 PASSED
tests/test_api.py::test_check_code_too_large_returns_413 PASSED
tests/test_api.py::test_check_timeout_returns_504 PASSED
tests/test_api.py::test_check_when_lean_unavailable_returns_503 PASSED

10 passed
```

## Endpoints

### `GET /health`

Verifica se o Lean (e o Lake) estão disponíveis para a API.

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

```json
{
    "status": "ok",
    "lean_available": true,
    "lean_version": "Lean (version 4.34.0, arm64-apple-darwin24.6.0, commit ..., Release)",
    "lake_available": true,
    "lake_version": "Lake version 5.0.0-src+... (Lean version 4.34.0)",
    "detail": null
}
```

Quando o Lean não está disponível, `status` vira `"unavailable"`,
`lean_available` vira `false` e `detail` traz uma explicação curta —
sempre com **HTTP 200** (o endpoint em si funcionou; ele só reporta que
o Lean não está OK).

### `POST /check`

Recebe `{ "code": "..." }` e retorna um relatório estruturado da
verificação.

**Requisição válida:**

```bash
curl -s -X POST http://127.0.0.1:8000/check \
  -H "Content-Type: application/json" \
  -d '{"code": "theorem zero_add (n : Nat) : 0 + n = n := by\n  induction n with\n  | zero => rfl\n  | succ n ih => simp [Nat.add, ih]\n"}' \
  | python3 -m json.tool
```

```json
{
    "valid": true,
    "exit_code": 0,
    "errors": [],
    "warnings": [
        {
            "line": 4,
            "column": 23,
            "severity": "warning",
            "message": "This simp argument is unused:\n  Nat.add\n\nHint: Omit it from the simp argument list.\n  [apply] simp [ih]\n\nNote: This linter can be disabled with `set_option linter.unusedSimpArgs false`"
        },
        {
            "line": 4,
            "column": 32,
            "severity": "warning",
            "message": "This simp argument is unused:\n  ih\n\n..."
        }
    ],
    "stdout": "Main.lean:4:23: warning: ...",
    "stderr": ""
}
```

(O exemplo `simp [Nat.add, ih]` compila, mas o `simp` do Lean core
considera os dois argumentos redundantes e emite *warnings* — daí
`valid: true` com `warnings` não vazio. Isso é esperado e ilustra a
diferença entre `errors` e `warnings` na resposta.)

**Requisição com prova inválida:**

```bash
curl -s -X POST http://127.0.0.1:8000/check \
  -H "Content-Type: application/json" \
  -d '{"code": "theorem broken : 1 + 1 = 3 := by decide\n"}' \
  | python3 -m json.tool
```

```json
{
    "valid": false,
    "exit_code": 1,
    "errors": [
        {
            "line": 1,
            "column": 33,
            "severity": "error",
            "message": "Tactic `decide` proved that the proposition\n  1 + 1 = 3\nis false"
        }
    ],
    "warnings": [],
    "stdout": "Main.lean:1:33: error: Tactic `decide` proved that the proposition\n  1 + 1 = 3\nis false\n",
    "stderr": ""
}
```

Note que essa é uma resposta **HTTP 200** — a requisição em si foi
processada com sucesso; é o *código Lean* que é inválido. `errors[0]`
já traz `line`/`column`/`severity`/`message` extraídos por parsing das
mensagens no formato `arquivo:linha:coluna: severidade: mensagem`
(além disso, `stdout`/`stderr` sempre preservam a saída bruta original,
já que o parser é *best-effort* e não precisa reconhecer 100% das
mensagens do Lean).

O caminho do arquivo temporário usado internamente pela API nunca
aparece na resposta — ele é substituído pelo nome lógico `Main.lean`
tanto no `stdout`/`stderr` quanto no parsing.

## Códigos de status HTTP

| Código | Quando |
|--------|--------|
| `200`  | O Lean rodou normalmente — inclusive quando o código tem erros de compilação ou uma prova inválida (nesse caso, `valid: false`). |
| `413`  | `code` excede `LEAN_MAX_CODE_SIZE` caracteres. |
| `422`  | Corpo da requisição malformado ou sem o campo `code` (validação padrão do FastAPI/Pydantic). |
| `503`  | O comando Lean/Lake configurado não pôde ser executado (não encontrado, sem permissão etc.). |
| `504`  | A execução do Lean excedeu `LEAN_TIMEOUT` segundos. |
| `500`  | Falha inesperada na própria API (sem detalhes internos na resposta; ver logs do processo). |

### Por que 504 para timeout (e não 408)?

`408 Request Timeout` descreve o *cliente* demorando demais para enviar
a requisição — não é o caso aqui, a requisição chega completa. `504
Gateway Timeout` descreve o servidor, atuando como intermediário, não
recebendo uma resposta a tempo de um processo upstream — que é
exatamente o papel da API em relação ao processo `lean` que ela invoca.
Por isso esta API usa **504** de forma consistente para timeout de
verificação.

## Logging

A API loga início/fim de requisições e falhas (`logging` padrão do
Python, nível `INFO`), mas **nunca loga o conteúdo completo do código
Lean enviado** — apenas metadados como o tamanho em caracteres. Isso
evita poluir logs com código potencialmente grande ou sensível.

## Limitações conhecidas

- **Sem sandbox real**: ver aviso de segurança acima.
- **Um processo Lean por requisição**: cada `POST /check` paga o custo
  de start-up do `lake env lean` (carregar o toolchain, `Init` etc.),
  o que é perceptível (tipicamente ~1–4s neste ambiente, variando com
  cache de disco/SO) mesmo para arquivos triviais. Não há reuso de
  estado entre requisições.
- **Parsing best-effort**: o parser de diagnósticos reconhece o padrão
  `arquivo:linha:coluna: severidade: mensagem` (incluindo variantes com
  um código entre parênteses, como `error(lean.someCode):`) e agrega
  linhas de continuação à mesma mensagem. Formatos de saída não
  previstos ainda aparecem em `stdout`/`stderr`, só não viram entradas
  estruturadas em `errors`/`warnings`.
- **Sem cancelamento de requisições concorrentes**: múltiplas
  requisições simultâneas disparam múltiplos processos Lean em
  paralelo; não há fila, limite de concorrência ou controle de recursos
  (CPU/memória) além do timeout por requisição.
- **`ℕ` (notação Unicode de Mathlib) não funciona sem Mathlib**: os
  exemplos e testes usam `Nat` (disponível em `Init`) em vez de `ℕ`,
  já que esta primeira versão não assume Mathlib instalado. Se
  `LEAN_PROJECT_DIR` apontar para um projeto com Mathlib como
  dependência, `ℕ` e outras notações passam a funcionar normalmente.

## Evolução futura: LSP/RPC

A implementação atual (`SubprocessLeanExecutor` em `app/lean_runner.py`)
satisfaz o protocolo `LeanExecutor`:

```python
class LeanExecutor(Protocol):
    def check(self, code: str) -> CheckResponse: ...
    def health(self) -> HealthResponse: ...
```

`app/main.py` depende apenas desse protocolo (via injeção de
dependência do FastAPI, `Depends(get_lean_executor)`) — nunca de
`subprocess` diretamente. Uma próxima versão pode introduzir um
`LspLeanExecutor` que mantém um **processo persistente do Lean Language
Server**, falando LSP/RPC (`textDocument/didOpen`,
`textDocument/publishDiagnostics` etc.) em vez de iniciar um processo
novo por requisição. Isso permitiria:

- reaproveitar o mesmo processo Lean entre requisições, eliminando o
  custo de start-up a cada chamada;
- diagnósticos incrementais (reverificar apenas o que mudou);
- manter estado de arquivos abertos entre requisições;
- recursos adicionais como `hover`, `completion` e, eventualmente,
  inspeção mais rica de *proof states* (relevante para um agente de IA
  que gera e corrige provas iterativamente).

Trocar a implementação seria uma questão de `get_lean_executor()`
retornar o novo executor — sem qualquer mudança em `main.py` ou
`models.py`.
