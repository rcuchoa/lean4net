#!/usr/bin/env python3
"""CLI em duas etapas para verificacao formal de infraestrutura de nuvem.

Etapa 1 (`generate`): le a especificacao YAML, pede ao LLM (Claude) um
programa Lean 4 que modela essa infraestrutura e prova as propriedades
exigidas, grava o `.lean` e o apresenta.

Etapa 2 (`verify`): recebe um `.lean` (o gerado na etapa 1, eventualmente
revisado a mao) e o submete ao compilador Lean 4, imprimindo um relatorio
PASS/FAIL por propriedade.

As duas etapas sao invocacoes independentes e sem estado compartilhado: o
unico elo entre elas e o proprio arquivo `.lean`, de onde a etapa 2
recalcula os intervalos de linha de cada propriedade a partir dos
marcadores `-- @property:PX`. Isso permite inspecionar (e ate corrigir) o
Lean antes de verificar, e reverificar quantas vezes for preciso sem
pagar uma nova chamada a API.

Uso:
    python3 main.py generate data/spec/example.yaml
    python3 main.py verify data/build/example.lean
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from backend.core.lean_codegen import (
    DEFAULT_PROPERTIES,
    LeanCodegenError,
    extract_sensitive_ports,
    generate,
    parse_properties,
)
from backend.core.model import SpecError
from backend.core.runner import find_lean_binary, run_lean
from backend.core.spec_loader import load_custom_properties, load_spec

DEFAULT_SENSITIVE_PORTS = [22, 3389]


def _info(msg: str = "") -> None:
    """Mensagem de progresso: vai para stderr para nao poluir o stdout.

    Na etapa 1 o stdout carrega apenas o codigo Lean, entao
    `main.py generate spec.yaml > infra.lean` produz um arquivo valido.
    """
    print(msg, file=sys.stderr)


def _parse_ports(text: str) -> list[int]:
    return [int(p.strip()) for p in text.split(",") if p.strip()]


def _load_properties_file(
    path: str | None, default: tuple[object, ...] = ()
) -> tuple[object, ...] | None:
    """Le e valida `--properties <arquivo.json>` (lista de propriedades:
    `[{"id": "C1", "description": "..."}]`). Devolve `None` em caso de erro
    (mensagem ja impressa em stderr) -- quem chama deve retornar `2` nesse
    caso, igual aos outros erros de configuracao da CLI. Sem `--properties`,
    devolve `default` -- em `cmd_generate` isso e `DEFAULT_PROPERTIES` (as
    quatro propriedades originais do projeto, mesmo texto usado para
    pre-popular o editor da webapp); em `cmd_verify` e `()`, porque
    `parse_properties` ja aceita de volta qualquer marcador `C<numero>`
    presente no arquivo mesmo sem a definicao original em maos.
    """
    if not path:
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as e:
        print(f"erro ao ler {path!r}: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"erro ao interpretar {path!r} como JSON: {e}", file=sys.stderr)
        return None
    try:
        return tuple(load_custom_properties(raw))
    except SpecError as e:
        print(f"erro nas propriedades customizadas ({path!r}): {e}", file=sys.stderr)
        return None


# --- etapa 1: geracao ---------------------------------------------------------


def cmd_generate(args: argparse.Namespace) -> int:
    try:
        sensitive_ports = _parse_ports(args.sensitive_ports)
    except ValueError:
        print(
            f"erro: lista de portas sensiveis invalida: {args.sensitive_ports!r} "
            "(use algo como 22,3389)",
            file=sys.stderr,
        )
        return 2

    try:
        spec = load_spec(args.spec)
    except SpecError as e:
        print(f"erro na especificacao: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # yaml malformado, arquivo ausente, etc.
        print(f"erro ao ler {args.spec!r}: {e}", file=sys.stderr)
        return 2

    custom_properties = _load_properties_file(args.properties, default=DEFAULT_PROPERTIES)
    if custom_properties is None:
        return 2

    out_path = args.out
    if out_path is None:
        base = os.path.splitext(os.path.basename(args.spec))[0]
        out_path = os.path.join("data", "build", f"{base}.lean")

    _info("Etapa 1/2 - gerando Lean 4 via LLM (Claude)...")
    try:
        lean_source, properties = generate(spec, sensitive_ports, custom_properties)
    except LeanCodegenError as e:
        print(f"erro ao gerar o Lean 4 via LLM: {e}", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(lean_source)

    _info()
    _info(f"--- Lean 4 gerado ({out_path}) ---")
    sys.stdout.write(lean_source)
    sys.stdout.flush()
    _info("--- fim ---")
    _info()

    width = max((len(p.theorem_name) for p in properties), default=0)
    for prop in properties:
        _info(
            f"  {prop.id:<3} {prop.theorem_name:<{width}}  "
            f"(linhas {prop.line_start}-{prop.line_end})"
        )
    _info()
    _info("Etapa 2/2 - revise o arquivo acima e rode o compilador Lean 4:")
    _info(f"    python3 main.py verify {out_path}")
    return 0


# --- etapa 2: verificacao -----------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        lean_bin = find_lean_binary(args.lean_bin)
    except FileNotFoundError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2

    try:
        with open(args.lean_file, "r", encoding="utf-8") as f:
            lean_source = f.read()
    except OSError as e:
        print(f"erro ao ler {args.lean_file!r}: {e}", file=sys.stderr)
        return 2

    # As portas sensiveis so alimentam o texto da descricao de P3 no
    # relatorio; le-las do proprio arquivo mantem o relatorio coerente com o
    # Lean que esta sendo verificado, mesmo que ele tenha sido gerado em
    # outra sessao com outras portas.
    sensitive_ports = extract_sensitive_ports(lean_source)
    if sensitive_ports is None:
        sensitive_ports = DEFAULT_SENSITIVE_PORTS

    custom_properties = _load_properties_file(args.properties)
    if custom_properties is None:
        return 2

    try:
        properties = parse_properties(lean_source, sensitive_ports, custom_properties)
    except LeanCodegenError as e:
        print(
            f"erro ao interpretar {args.lean_file!r}: {e}\n"
            "Este comando espera um arquivo produzido por 'main.py generate' "
            "(com os marcadores '-- @property:PX').",
            file=sys.stderr,
        )
        return 2

    print(f"Etapa 2/2 - validando {args.lean_file} com: {lean_bin}")
    print()

    report = run_lean(args.lean_file, properties, lean_bin)

    width = max((len(p.theorem_name) for p in properties), default=0)
    for result in report.results:
        status = "PASS" if result.ok else "FAIL"
        marker = "✓" if result.ok else "✗"
        print(
            f"[{status}] {marker} {result.prop.id:<3} "
            f"{result.prop.theorem_name:<{width}}  {result.prop.description}"
        )
        for msg in result.messages:
            for line in msg.splitlines():
                print(f"         > {line}")

    print()
    if report.success:
        print("Todas as propriedades foram verificadas pelo compilador Lean 4. QED.")
    else:
        print("Uma ou mais propriedades NAO puderam ser provadas (ver detalhes acima).")
        if report.raw_stderr.strip() and not report.results:
            print("--- stderr bruto do lean ---")
            print(report.raw_stderr)

    return 0 if report.success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verificacao formal de infraestrutura de nuvem em duas etapas: "
            "'generate' escreve o Lean 4 a partir do YAML; 'verify' submete "
            "esse Lean ao compilador."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="{generate,verify}")

    gen = sub.add_parser(
        "generate",
        help="etapa 1: gera o Lean 4 a partir do YAML e o apresenta",
        description=(
            "Etapa 1: le o YAML, gera o programa Lean 4 via LLM, grava em "
            "--out e imprime o codigo (stdout) para inspecao. Nao chama o "
            "compilador."
        ),
    )
    gen.add_argument("spec", help="caminho para o YAML da infraestrutura")
    gen.add_argument(
        "--out",
        default=None,
        help="onde escrever o .lean gerado (default: data/build/<nome-da-spec>.lean)",
    )
    gen.add_argument(
        "--sensitive-ports",
        default=",".join(str(p) for p in DEFAULT_SENSITIVE_PORTS),
        help=(
            "lista de portas (separadas por virgula) que nao podem ficar "
            "abertas para 0.0.0.0/0"
        ),
    )
    gen.add_argument(
        "--properties",
        default=None,
        metavar="propriedades.json",
        help=(
            "arquivo JSON com as propriedades a provar, em linguagem "
            'natural: [{"id": "C1", "description": "..."}, ...] -- id no '
            "formato 'C<numero>'. Sem esta opcao, usa as 4 propriedades "
            "originais do projeto (subnets dentro da VPC, sem sobreposicao, "
            "sem portas sensiveis abertas, referencias validas)"
        ),
    )
    gen.set_defaults(func=cmd_generate)

    ver = sub.add_parser(
        "verify",
        help="etapa 2: verifica um .lean com o compilador Lean 4",
        description=(
            "Etapa 2: submete o .lean ao compilador Lean 4 e imprime o "
            "relatorio PASS/FAIL por propriedade. Nao chama a API da "
            "Anthropic -- pode ser repetido a vontade sobre o mesmo arquivo."
        ),
    )
    ver.add_argument("lean_file", metavar="arquivo.lean", help="o .lean produzido pela etapa 1")
    ver.add_argument("--lean-bin", default=None, help="caminho para o executavel 'lean'")
    ver.add_argument(
        "--properties",
        default=None,
        metavar="propriedades.json",
        help=(
            "mesmo arquivo usado em 'generate --properties' -- opcional, so "
            "melhora a descricao de propriedades customizadas no relatorio "
            "(o resultado da verificacao em si nao depende disso)"
        ),
    )
    ver.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
