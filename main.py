#!/usr/bin/env python3
"""CLI: recebe uma especificacao YAML de infraestrutura de nuvem, gera um
programa Lean 4 que modela essa infraestrutura e prova propriedades sobre
ela, executa o compilador Lean para validar as provas, e imprime um
relatorio human-readable.

Uso:
    python3 main.py spec/example.yaml
    python3 main.py spec/example_bad.yaml --out build/example_bad.lean
"""

from __future__ import annotations

import argparse
import os
import sys

from verifier.lean_codegen import generate
from verifier.model import SpecError
from verifier.runner import find_lean_binary, run_lean
from verifier.spec_loader import load_spec

DEFAULT_SENSITIVE_PORTS = [22, 3389]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", help="caminho para o YAML da infraestrutura")
    parser.add_argument(
        "--out",
        default=None,
        help="onde escrever o .lean gerado (default: build/<nome-da-spec>.lean)",
    )
    parser.add_argument("--lean-bin", default=None, help="caminho para o executavel 'lean'")
    parser.add_argument(
        "--sensitive-ports",
        default=",".join(str(p) for p in DEFAULT_SENSITIVE_PORTS),
        help="lista de portas (separadas por virgula) que nao podem ficar abertas para 0.0.0.0/0",
    )
    args = parser.parse_args(argv)

    try:
        lean_bin = find_lean_binary(args.lean_bin)
    except FileNotFoundError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2

    try:
        spec = load_spec(args.spec)
    except SpecError as e:
        print(f"erro na especificacao: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # yaml malformado, arquivo ausente, etc.
        print(f"erro ao ler {args.spec!r}: {e}", file=sys.stderr)
        return 2

    sensitive_ports = [int(p) for p in args.sensitive_ports.split(",") if p.strip()]

    lean_source, properties = generate(spec, sensitive_ports)

    out_path = args.out
    if out_path is None:
        base = os.path.splitext(os.path.basename(args.spec))[0]
        out_path = os.path.join("build", f"{base}.lean")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(lean_source)

    print(f"Programa Lean gerado em: {out_path}")
    print(f"Validando prova com: {lean_bin}")
    print()

    report = run_lean(out_path, properties, lean_bin)

    width = max((len(p.theorem_name) for p in properties), default=0)
    for result in report.results:
        status = "PASS" if result.ok else "FAIL"
        marker = "✓" if result.ok else "✗"
        print(f"[{status}] {marker} {result.prop.id:<3} {result.prop.theorem_name:<{width}}  {result.prop.description}")
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


if __name__ == "__main__":
    raise SystemExit(main())
