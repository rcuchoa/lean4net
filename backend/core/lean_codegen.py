"""Gera um arquivo Lean 4 autocontido usando um LLM da Anthropic (Claude).

Ao contrario da versao anterior deste modulo (que montava o `.lean` por
concatenacao de templates em Python), aqui o codigo Lean -- estruturas,
dados da especificacao embutidos como termos, funcoes auxiliares e os
teoremas `by decide` -- e integralmente escrito pelo modelo, a partir de
uma descricao precisa (em linguagem natural + JSON) da especificacao e das
propriedades exigidas. O compilador Lean (`backend/core/runner.py`) continua
sendo a autoridade final: se o modelo errar a traducao dos dados ou a
semantica de uma propriedade, o `theorem` correspondente falha a
elaborar e isso aparece como FAIL no relatorio, exatamente como aconteceria
com um erro humano.

Para que `runner.py` continue conseguindo ligar um erro de compilacao (que
so vem com numero de linha) de volta a propriedade correspondente, pedimos
ao modelo para envolver cada teorema com marcadores em comentario Lean
(`-- @property:C1` / `-- @end_property:C1`). O intervalo de linhas de cada
propriedade e calculado aqui, em Python, varrendo o texto *realmente*
devolvido pelo modelo -- nunca confiamos em um numero de linha que o
proprio modelo eventualmente reporte.

Nao ha mais um conjunto "fixo" de propriedades embutido no prompt: TODAS as
propriedades (as quatro originais inclusive) vem de uma lista de
`CustomProperty` fornecida por quem chama (`--properties` na CLI, o editor
na webapp). `DEFAULT_PROPERTIES`, abaixo, e apenas o texto usado para
pre-popular essa lista quando ninguem fornece nada -- nao e mais tratado
de forma especial pelo gerador ou pelo parser.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import anthropic

from backend.core.model import CustomProperty, InfraSpec

MODEL = "claude-opus-5"
MAX_TOKENS = 16000
THINKING = {"type": "adaptive"}
OUTPUT_CONFIG = {"effort": "high"}


@dataclass(frozen=True)
class PromptPreview:
    """Exatamente o que sera enviado ao modelo em `messages.create`.

    Existe para que a interface possa mostrar o prompt ANTES de gastar uma
    chamada de API. Como `_call_llm` monta a requisicao a partir desta mesma
    estrutura (e nao de constantes repetidas), o que o usuario ve e o que
    de fato vai para o modelo -- os dois nao tem como divergir.
    """

    model: str
    max_tokens: int
    thinking: dict
    output_config: dict
    system: str
    user: str


@dataclass(frozen=True)
class Property:
    id: str
    theorem_name: str
    description: str
    line_start: int
    line_end: int


class LeanCodegenError(Exception):
    """Erro ao gerar o Lean 4 (falha de API) ou ao parsear a resposta do modelo."""


# --- propriedades padrao (pre-populam o editor; nao sao mais "fixas") --------
# Mesmo texto das quatro propriedades originais deste projeto, agora
# expressas so como descricao em linguagem natural -- o modelo formaliza
# cada uma exatamente como formalizaria qualquer propriedade adicionada
# pelo usuario. Servem apenas de ponto de partida: podem ser editadas,
# apagadas ou complementadas livremente (ver `--properties` na CLI e o
# editor de propriedades na webapp).
DEFAULT_PROPERTIES: tuple[CustomProperty, ...] = (
    CustomProperty(
        "C1",
        "Toda subnet esta contida dentro do CIDR da sua propria VPC (o CIDR "
        "da subnet esta contido no CIDR da VPC que ela referencia).",
    ),
    CustomProperty(
        "C2",
        "Nenhum par de subnets distintas que pertencem a mesma VPC tem "
        "faixas de IP (CIDR) sobrepostas entre si.",
    ),
    CustomProperty(
        "C3",
        "Nenhuma regra de ingress de nenhum security group cobre uma porta "
        "sensivel (22 e 3389 por padrao) e ao mesmo tempo libera a origem "
        "para o mundo todo (0.0.0.0/0).",
    ),
    CustomProperty(
        "C4",
        "Toda instancia referencia, por nome, uma subnet que de fato existe "
        "na especificacao, e todos os security groups que ela referencia "
        "tambem existem na especificacao.",
    ),
)


_SYSTEM_PROMPT = """\
Voce e um especialista em Lean 4 (sem Mathlib -- apenas a biblioteca `Init`,
que ja vem com qualquer instalacao do toolchain). Sua tarefa e escrever um
UNICO arquivo Lean 4 autocontido, que modela uma infraestrutura de nuvem
virtual e prova, via `decide` (ou `native_decide` caso `decide` nao seja
viavel), as propriedades sobre ela listadas na mensagem do usuario.

Requisitos do arquivo:

1. Defina estas `structure`s (nomes e campos exatamente assim, pois humanos
   inspecionam o arquivo gerado):
     structure Cidr where { addr : Nat; plen : Nat } deriving DecidableEq, Repr
     structure Rule where { proto : String; portFrom : Nat; portTo : Nat; srcCidr : Cidr } deriving Repr
     structure Vpc where { name : String; cidr : Cidr } deriving Repr
     structure Subnet where { name : String; vpc : String; cidr : Cidr } deriving Repr
     structure SecGroup where { name : String; rules : List Rule } deriving Repr
     structure Instance where { name : String; subnet : String; sgs : List String } deriving Repr

2. Implemente estas funcoes auxiliares com a semantica EXATA abaixo (a
   corretude das propriedades depende disso) -- use-as sempre que uma
   propriedade da mensagem do usuario precisar delas:
   - `netMask (p : Nat) : Nat` -- mascara de rede de 32 bits para o prefixo
     `p` (bits mais significativos em 1): `(2^32 - 1) - (2^(32-p) - 1)`.
   - `netAddr (c : Cidr) : Nat` -- `c.addr` com a mascara `netMask c.plen`
     aplicada via AND bit a bit (`Nat.land`).
   - `cidrContains (outer inner : Cidr) : Bool` -- verdadeiro sse
     `outer.plen <= inner.plen` E o endereco de rede de `inner` calculado
     com a mascara de `outer` e igual a `netAddr outer`.
   - `cidrOverlaps (a b : Cidr) : Bool` -- verdadeiro sse, usando o menor
     dos dois prefixos `p := min a.plen b.plen` e a mascara `netMask p`,
     `a.addr` e `b.addr` caem na mesma rede.
   - `coversPort (r : Rule) (port : Nat) : Bool` -- `r.portFrom <= port <= r.portTo`.
   - `isOpenToWorld (r : Rule) : Bool` -- verdadeiro sse `r.srcCidr.plen == 0`
     (equivalente a origem `0.0.0.0/0`).

3. Embuta os dados da especificacao (fornecidos abaixo em JSON, com os CIDRs
   ja convertidos para `addr`/`plen`) como `def`s Lean:
   `def vpcs : List Vpc := [...]`, `def subnets : List Subnet := [...]`,
   `def secGroups : List SecGroup := [...]`, `def instances : List Instance := [...]`,
   `def sensitivePorts : List Nat := [...]`. Copie os valores de `addr`/`plen`
   literalmente -- nao recalcule a partir de string.

4. A mensagem do usuario lista as propriedades a provar sobre esta
   infraestrutura -- cada uma com um "id" (ex.: C1), um "nome do teorema"
   EXATO a usar, e uma descricao em linguagem natural do que deve ser
   garantido. Para CADA propriedade listada:

   - Implemente as funcoes booleanas auxiliares necessarias (reaproveitando
     as da secao 2 sempre que possivel, ou criando novas quando a descricao
     exigir algo alem delas).
   - Formule um enunciado Lean preciso que capture a descricao dada.
   - Declare-o como um `theorem` usando o nome de teorema EXATO indicado
     (nunca invente outro nome).
   - Prove-o por `decide` (ou `native_decide` caso `decide` nao seja viavel).

   Cada enunciado deve ser formulado como uma proposicao POSITIVA que
   termina em `= true` (ex.: `algumaFuncaoBooleana args = true`, ou
   `∀ x ∈ lista, predicado x = true`) -- nunca como `= false`, nunca
   envolvendo a proposicao inteira em `¬` (not), e nunca usando
   `Decidable.decide` ou qualquer outro truque para inverter o sentido da
   afirmacao.

   REGRA CRITICA, NAO NEGOCIAVEL: o enunciado de cada teorema e FIXO pela
   descricao fornecida na mensagem do usuario -- ele NAO deve ser ajustado,
   invertido ou reformulado com base em a especificacao fornecida
   efetivamente satisfazer essa propriedade ou nao. Se, ao analisar os
   dados, voce perceber que a especificacao VIOLA uma propriedade, o
   comportamento CORRETO e escrever o enunciado positivo de qualquer forma
   e deixar `decide`/`native_decide` falhar ao elaborar o teorema -- essa
   falha de compilacao E o resultado esperado e correto (sera reportado
   como uma violacao real para quem usa a ferramenta). Reescrever o
   enunciado para a negacao so para conseguir uma prova que fecha, quando a
   propriedade real e falsa, transformaria uma violacao de seguranca real
   (ex.: uma porta sensivel aberta para `0.0.0.0/0`) em um falso "verificado
   com sucesso" -- e o pior erro possivel que este gerador pode cometer.
   Nunca faca isso.

5. Envolva a declaracao de CADA teorema (do `theorem` ate o fim da prova,
   inclusive) com marcadores em linhas de comentario proprias, exatamente
   neste formato (troque apenas o ID):

       -- @property:C1
       theorem customProp_C1 :
           ... := by decide
       -- @end_property:C1

   Os marcadores devem aparecer em uma linha isolada, sem mais nada alem
   deles (nem outro comentario, nem codigo). Use um par por teorema, na
   MESMA ordem em que as propriedades foram listadas na mensagem do
   usuario, sem aninhar ou pular nenhuma.

6. Sua resposta deve conter SOMENTE o codigo-fonte Lean 4, do inicio ao fim.
   Nao inclua explicacoes, nao inclua marcacao markdown (` ```lean ` ou
   ` ``` `), nao inclua nenhum texto fora do arquivo `.lean`.
"""


def _user_prompt(
    spec: InfraSpec,
    sensitive_ports: list[int],
    custom_properties: tuple[CustomProperty, ...] = (),
) -> str:
    payload = {
        "vpcs": [
            {"name": v.name, "cidr": {"addr": v.cidr.addr, "plen": v.cidr.plen}}
            for v in spec.vpcs
        ],
        "subnets": [
            {
                "name": s.name,
                "vpc": s.vpc,
                "cidr": {"addr": s.cidr.addr, "plen": s.cidr.plen},
            }
            for s in spec.subnets
        ],
        "security_groups": [
            {
                "name": sg.name,
                "rules": [
                    {
                        "proto": r.proto,
                        "port_from": r.port_from,
                        "port_to": r.port_to,
                        "src_cidr": {"addr": r.src.addr, "plen": r.src.plen},
                    }
                    for r in sg.rules
                ],
            }
            for sg in spec.security_groups
        ],
        "instances": [
            {"name": i.name, "subnet": i.subnet, "security_groups": i.security_groups}
            for i in spec.instances
        ],
        "sensitive_ports": sensitive_ports,
    }

    if custom_properties:
        props_text = "\n\n".join(
            f"- id: {c.id}\n  nome do teorema: {c.theorem_name}\n  descricao: {c.description}"
            for c in custom_properties
        )
    else:
        props_text = "(nenhuma propriedade foi definida)"

    return "\n\n".join(
        [
            "Especificacao (JSON), CIDRs ja em addr/plen (inteiros de 32 bits):\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2),
            "Propriedades a provar (ver secao 4 do system prompt -- formule e "
            "prove um teorema para cada uma, na ordem abaixo):\n\n" + props_text,
        ]
    )


def build_prompt(
    spec: InfraSpec,
    sensitive_ports: list[int],
    custom_properties: tuple[CustomProperty, ...] = (),
) -> PromptPreview:
    """Monta a requisicao que sera enviada ao modelo, sem envia-la.

    Usada por `_call_llm` (para de fato chamar a API) e pela interface (para
    apresentar o prompt antes da geracao).
    """
    return PromptPreview(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking=THINKING,
        output_config=OUTPUT_CONFIG,
        system=_SYSTEM_PROMPT,
        user=_user_prompt(spec, sensitive_ports, custom_properties),
    )


# --- chamada ao modelo --------------------------------------------------------


def _call_llm(
    spec: InfraSpec,
    sensitive_ports: list[int],
    custom_properties: tuple[CustomProperty, ...] = (),
) -> str:
    prompt = build_prompt(spec, sensitive_ports, custom_properties)
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=prompt.model,
            max_tokens=prompt.max_tokens,
            thinking=prompt.thinking,
            output_config=prompt.output_config,
            system=prompt.system,
            messages=[{"role": "user", "content": prompt.user}],
        )
    except anthropic.AuthenticationError as exc:
        raise LeanCodegenError(
            "falha de autenticacao com a API da Anthropic -- defina ANTHROPIC_API_KEY "
            "ou faca login com 'ant auth login'."
        ) from exc
    except anthropic.APIError as exc:
        raise LeanCodegenError(f"falha ao chamar a API da Anthropic: {exc}") from exc
    except Exception as exc:
        # Cobre falhas que o SDK nao expressa como anthropic.APIError -- por
        # exemplo, um TypeError levantado ainda na resolucao de credenciais
        # quando nenhuma API key/token/perfil esta configurado no processo.
        # Sem este catch-all, a excecao escaparia de generate() e o servidor
        # web devolveria uma pagina HTML de erro em vez de JSON (o frontend
        # falharia com algo como "Unexpected token '<' ... is not valid JSON").
        raise LeanCodegenError(
            f"falha inesperada ao chamar a API da Anthropic: {exc}"
        ) from exc

    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", None)
        raise LeanCodegenError(
            "o modelo recusou gerar o codigo Lean para esta especificacao"
            + (f": {detail}" if detail else ".")
        )

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise LeanCodegenError("o modelo nao devolveu nenhum texto.")
    return text


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n(?P<body>.*)\n```$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    m = _FENCE_RE.match(text.strip())
    return m.group("body") if m else text


# --- parsing dos marcadores ---------------------------------------------------


def _marker_re(kind: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*--\s*@{kind}:(?P<id>\S+)\s*$")


_BEGIN_RE = _marker_re("property")
_END_RE = _marker_re("end_property")

# Marca onde termina o enunciado (proposicao) e comeca a tatica de prova.
_PROOF_RE = re.compile(r"\bby\s+(decide|native_decide)\b")
_FALSE_RE = re.compile(r"\bfalse\b", re.IGNORECASE)
_TRUE_GOAL_RE = re.compile(r"=\s*true\b")
_THEOREM_NAME_RE = re.compile(r"\btheorem\s+(\S+)")


def _validate_statement_is_positive(pid: str, name: str, block_text: str) -> None:
    """Recusa teoremas que provam a NEGACAO da propriedade em vez da propriedade.

    O modelo pode, ao perceber que a spec de fato viola uma propriedade,
    reescrever o enunciado como a negacao (ex.: `algo = false` em vez de
    `algo = true`) so para obter uma prova trivial -- isso disfarcaria uma
    violacao real de seguranca como "verificado com sucesso". Esta checagem
    e a ultima linha de defesa contra esse caso, independente do que o
    prompt pediu. Como ela roda dentro de `parse_properties`, tambem cobre
    a etapa de verificacao: um `.lean` editado a mao com o mesmo truque e
    recusado antes de chegar ao compilador.
    """
    if name not in block_text:
        raise LeanCodegenError(
            f"{pid}: o bloco marcado nao contem 'theorem {name}' -- nome de "
            "teorema inesperado ou fora de ordem."
        )

    proof_match = _PROOF_RE.search(block_text)
    if not proof_match:
        raise LeanCodegenError(
            f"{pid} ({name}): prova nao usa 'by decide'/'by native_decide' -- "
            "nao e possivel validar automaticamente."
        )

    statement = block_text[: proof_match.start()]
    if _FALSE_RE.search(statement):
        raise LeanCodegenError(
            f"{pid} ({name}): o enunciado do teorema menciona 'false' -- isso "
            "normalmente indica que a propriedade foi reformulada para provar "
            "a negacao dela (fazendo uma violacao real parecer 'PASS'). "
            "Enunciado recusado por seguranca."
        )
    if not _TRUE_GOAL_RE.search(statement):
        raise LeanCodegenError(
            f"{pid} ({name}): o enunciado do teorema nao afirma '... = true' -- "
            "formato inesperado, recusado por seguranca."
        )


_SENSITIVE_PORTS_DEF_RE = re.compile(
    r"^\s*def\s+sensitivePorts\s*:\s*List\s+Nat\s*:=\s*\[(?P<items>[^\]]*)\]",
    re.MULTILINE,
)


def extract_sensitive_ports(lean_source: str) -> list[int] | None:
    """Le de volta a lista `def sensitivePorts : List Nat := [...]` do .lean.

    Usado pela etapa de verificacao, que recebe apenas o arquivo Lean (e nao
    a spec YAML nem as opcoes da etapa de geracao) -- as portas sensiveis so
    entram como dado auxiliar para propriedades que precisem delas, e le-las
    do proprio arquivo que esta sendo verificado mantem o relatorio fiel ao
    que o Lean de fato afirma. Devolve `None` se o `def` nao existir ou nao
    for uma lista de literais numericos -- nesse caso cabe a quem chama
    escolher um default.
    """
    m = _SENSITIVE_PORTS_DEF_RE.search(lean_source)
    if not m:
        return None
    try:
        return [int(tok.strip()) for tok in m.group("items").split(",") if tok.strip()]
    except ValueError:
        return None


# Aceita tanto o formato atual (`C<numero>`) quanto o formato legado
# (`P<numero>`, usado antes da unificacao das propriedades fixas com as
# customizadas) -- so para RECONHECER o marcador em um `.lean` ja existente;
# toda propriedade nova (CLI/webapp) sempre usa `C<numero>`.
_CUSTOM_ID_RE = re.compile(r"^[CP]\d+$")


def parse_properties(
    lean_source: str,
    sensitive_ports: list[int],
    custom_properties: tuple[CustomProperty, ...] = (),
) -> list[Property]:
    """Localiza os marcadores `-- @property:ID` e devolve o intervalo de linhas de cada propriedade.

    Chamado tanto no fim da geracao quanto no inicio da verificacao (a etapa
    2 recebe so o `.lean`, possivelmente editado a mao, e precisa recalcular
    os intervalos sobre o texto atual do arquivo). Como a validacao de
    `_validate_statement_is_positive` acontece aqui, ela tambem roda de novo
    a cada verificacao -- um arquivo editado para provar a negacao de uma
    propriedade e recusado antes mesmo de chamar o compilador.

    Nao existe mais um conjunto fixo de propriedades sempre exigidas: toda
    propriedade em `custom_properties` e exigida (erro se faltar), e
    qualquer OUTRO marcador com id no formato `C<numero>` tambem e aceito
    (mas nao exigido) -- por exemplo, ao verificar um `.lean` salvo em disco
    sem reenviar a lista de propriedades do editor. O nome do teorema e
    sempre reconstruivel a partir do id (`customProp_<id>`, ver
    `CustomProperty.theorem_name`), entao a validacao de enunciado positivo
    funciona igual em ambos os casos; so a descricao em linguagem natural
    fica indisponivel quando a definicao original nao foi fornecida.
    """
    custom_by_id = {c.id: c for c in custom_properties}

    lines = lean_source.splitlines()
    open_id: str | None = None
    open_start: int | None = None
    found: dict[str, tuple[int, int]] = {}
    order: list[str] = []

    for idx, line in enumerate(lines, start=1):
        begin = _BEGIN_RE.match(line)
        end = _END_RE.match(line)
        if begin:
            if open_id is not None:
                raise LeanCodegenError(
                    f"marcador '@property:{begin.group('id')}' na linha {idx} "
                    f"apareceu antes do '@end_property:{open_id}' correspondente."
                )
            pid = begin.group("id")
            if not _CUSTOM_ID_RE.match(pid):
                raise LeanCodegenError(f"marcador para propriedade desconhecida: {pid!r}")
            if pid in found:
                raise LeanCodegenError(f"propriedade {pid!r} marcada mais de uma vez.")
            open_id = pid
            open_start = idx + 1  # o teorema comeca na linha seguinte ao marcador
        elif end:
            pid = end.group("id")
            if open_id is None or pid != open_id:
                raise LeanCodegenError(
                    f"'@end_property:{pid}' na linha {idx} sem '@property:{pid}' correspondente."
                )
            found[pid] = (open_start, idx - 1)  # exclui a linha do proprio marcador de fim
            order.append(pid)
            open_id = None
            open_start = None

    if open_id is not None:
        raise LeanCodegenError(f"'@property:{open_id}' nunca foi fechado com '@end_property:{open_id}'.")

    missing = [c.id for c in custom_properties if c.id not in found]
    if missing:
        raise LeanCodegenError(
            f"propriedades sem par de marcadores '-- @property:ID': {missing}"
        )

    properties: list[Property] = []
    for pid in order:
        start, end_line = found[pid]
        if end_line < start:
            raise LeanCodegenError(f"intervalo de linhas vazio/invalido para {pid!r}.")
        block_text = "\n".join(lines[start - 1 : end_line])

        if pid in custom_by_id:
            name, desc = custom_by_id[pid].theorem_name, custom_by_id[pid].description
        else:
            # Sem a definicao original (verificando um .lean salvo sem
            # reenviar a lista de propriedades, ou um arquivo legado com
            # marcadores `P<numero>` de antes da unificacao): o nome do
            # teorema e lido do proprio bloco em vez de adivinhado, ja que
            # so arquivos gerados apos essa unificacao seguem a convencao
            # `customProp_<id>`.
            name_match = _THEOREM_NAME_RE.search(block_text)
            name = name_match.group(1) if name_match else f"customProp_{pid}"
            desc = (
                "(propriedade customizada -- descricao original nao "
                "fornecida nesta verificacao)"
            )
        _validate_statement_is_positive(pid, name, block_text)
        properties.append(Property(pid, name, desc, start, end_line))

    return properties


# --- entrada publica -----------------------------------------------------------


def generate(
    spec: InfraSpec,
    sensitive_ports: list[int],
    custom_properties: tuple[CustomProperty, ...] = (),
) -> tuple[str, list[Property]]:
    """Gera o `.lean` chamando o Claude e devolve (fonte, propriedades).

    Levanta `LeanCodegenError` se `custom_properties` estiver vazio (nao ha
    o que provar), se a chamada a API falhar, ou se a resposta do modelo nao
    contiver os marcadores esperados para cada propriedade em
    `custom_properties`. O texto devolvido e sempre o que o modelo escreveu
    (sem pos-edicao alem de remover uma eventual cerca de codigo markdown)
    -- a validacao de corretude do Lean em si fica inteiramente a cargo do
    compilador, em `backend/core/runner.py`.
    """
    if not custom_properties:
        raise LeanCodegenError(
            "nenhuma propriedade foi definida para verificar -- adicione ao "
            "menos uma antes de gerar o Lean."
        )

    raw = _call_llm(spec, sensitive_ports, custom_properties)
    lean_source = _strip_code_fence(raw)
    properties = parse_properties(lean_source, sensitive_ports, custom_properties)
    if not lean_source.endswith("\n"):
        lean_source += "\n"
    return lean_source, properties
