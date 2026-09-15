"""Gera um arquivo Lean 4 autocontido que:

  1. Define os tipos e as funcoes de suporte (aritmetica de CIDR, checagem
     de portas etc.) -- uma pequena "biblioteca" fixa.
  2. Embute os dados da especificacao como termos Lean (listas de VPCs,
     subnets, security groups, instancias).
  3. Declara um teorema por propriedade, provado por `decide`: o kernel do
     Lean avalia a proposicao decidivel e so aceita o `theorem` se ela
     reduzir para `true`. Se uma propriedade for falsa, `lean` rejeita o
     arquivo com um erro -- e e exatamente esse erro que usamos para
     reportar qual propriedade falhou.

O modulo tambem devolve um mapa de (linha_inicio, linha_fim) -> Property,
para que o runner consiga ligar erros do compilador (que vem com
numero de linha) de volta a propriedade correspondente.
"""

from __future__ import annotations

from dataclasses import dataclass

from verifier.model import Cidr, InfraSpec


@dataclass(frozen=True)
class Property:
    id: str
    theorem_name: str
    description: str
    line_start: int
    line_end: int


_PRELUDE = '''\
/-
  Arquivo gerado automaticamente. Nao edite a mao -- ele e reconstruido a
  cada execucao a partir da especificacao YAML de entrada.

  Este arquivo modela uma infraestrutura de nuvem virtual (VPCs, subnets,
  security groups e instancias) e prova, via `decide`, propriedades
  estaticas sobre ela. `decide` funciona porque cada propriedade abaixo e
  formulada como uma proposicao *decidivel* sobre dados finitos: o kernel
  do Lean simplesmente avalia a funcao booleana correspondente e checa que
  o resultado e `true`. Se o Lean aceitar o arquivo (exit code 0), todas as
  propriedades foram matematicamente verificadas para esta especificacao
  exata.
-/

structure Cidr where
  addr : Nat  -- endereco IPv4 como inteiro de 32 bits
  plen : Nat  -- tamanho do prefixo, 0..32
deriving DecidableEq, Repr

structure Rule where
  proto     : String
  portFrom  : Nat
  portTo    : Nat
  srcCidr   : Cidr
deriving Repr

structure Vpc where
  name : String
  cidr : Cidr
deriving Repr

structure Subnet where
  name : String
  vpc  : String
  cidr : Cidr
deriving Repr

structure SecGroup where
  name  : String
  rules : List Rule
deriving Repr

structure Instance where
  name : String
  subnet : String
  sgs  : List String
deriving Repr

/-- Mascara de rede (32 bits) para um prefixo `p` entre 0 e 32. -/
def netMask (p : Nat) : Nat := (2 ^ 32) - 1 - (2 ^ (32 - p) - 1)

/-- Endereco de rede de um CIDR (aplica a mascara ao endereco). -/
def netAddr (c : Cidr) : Nat := Nat.land c.addr (netMask c.plen)

/-- `contains outer inner` : o bloco `inner` esta inteiramente dentro de `outer`. -/
def cidrContains (outer inner : Cidr) : Bool :=
  outer.plen <= inner.plen && netAddr outer == Nat.land inner.addr (netMask outer.plen)

/-- Dois CIDRs se sobrepoem se compartilham algum endereco. -/
def cidrOverlaps (a b : Cidr) : Bool :=
  let p := min a.plen b.plen
  let m := netMask p
  Nat.land a.addr m == Nat.land b.addr m

def coversPort (r : Rule) (port : Nat) : Bool :=
  r.portFrom <= port && port <= r.portTo

/-- Regra libera a origem "0.0.0.0/0" (qualquer endereco da internet). -/
def isOpenToWorld (r : Rule) : Bool := r.srcCidr.plen == 0
'''


def _lean_string(s: str) -> str:
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _lean_cidr(c: Cidr) -> str:
    return f"{{ addr := {c.addr}, plen := {c.plen} }}"


def generate(spec: InfraSpec, sensitive_ports: list[int]) -> tuple[str, list[Property]]:
    lines: list[str] = _PRELUDE.splitlines()
    lines.append("")

    # --- dados da especificacao -------------------------------------------------
    lines.append("def vpcs : List Vpc := [")
    for v in spec.vpcs:
        lines.append(f"  {{ name := {_lean_string(v.name)}, cidr := {_lean_cidr(v.cidr)} }},")
    lines.append("]")
    lines.append("")

    lines.append("def subnets : List Subnet := [")
    for s in spec.subnets:
        lines.append(
            f"  {{ name := {_lean_string(s.name)}, vpc := {_lean_string(s.vpc)}, "
            f"cidr := {_lean_cidr(s.cidr)} }},"
        )
    lines.append("]")
    lines.append("")

    lines.append("def secGroups : List SecGroup := [")
    for sg in spec.security_groups:
        rule_terms = []
        for r in sg.rules:
            rule_terms.append(
                f"{{ proto := {_lean_string(r.proto)}, portFrom := {r.port_from}, "
                f"portTo := {r.port_to}, srcCidr := {_lean_cidr(r.src)} }}"
            )
        rules_str = ", ".join(rule_terms)
        lines.append(f"  {{ name := {_lean_string(sg.name)}, rules := [{rules_str}] }},")
    lines.append("]")
    lines.append("")

    lines.append("def instances : List Instance := [")
    for i in spec.instances:
        sgs_str = ", ".join(_lean_string(s) for s in i.security_groups)
        lines.append(
            f"  {{ name := {_lean_string(i.name)}, subnet := {_lean_string(i.subnet)}, "
            f"sgs := [{sgs_str}] }},"
        )
    lines.append("]")
    lines.append("")

    sensitive_str = ", ".join(str(p) for p in sensitive_ports)
    lines.append(f"def sensitivePorts : List Nat := [{sensitive_str}]")
    lines.append("")

    # --- funcoes auxiliares para as propriedades --------------------------------
    lines += [
        "def findVpc (name : String) : Option Vpc :=",
        "  vpcs.find? (fun v => v.name == name)",
        "",
        "def subnetWithinItsVpc (s : Subnet) : Bool :=",
        "  match findVpc s.vpc with",
        "  | some v => cidrContains v.cidr s.cidr",
        "  | none => false",
        "",
        "def subnetPairOk (a b : Subnet) : Bool :=",
        "  if a.name == b.name then true",
        "  else if a.vpc == b.vpc then !(cidrOverlaps a.cidr b.cidr)",
        "  else true",
        "",
        "def ruleExposesSensitivePort (r : Rule) : Bool :=",
        "  isOpenToWorld r && sensitivePorts.any (coversPort r)",
        "",
        "def sgIsSafe (sg : SecGroup) : Bool :=",
        "  sg.rules.all (fun r => !(ruleExposesSensitivePort r))",
        "",
        "def subnetExists (name : String) : Bool := subnets.any (fun s => s.name == name)",
        "def sgExists (name : String) : Bool := secGroups.any (fun sg => sg.name == name)",
        "",
        "def instanceRefsOk (i : Instance) : Bool :=",
        "  subnetExists i.subnet && i.sgs.all sgExists",
        "",
    ]

    # --- teoremas (uma propriedade por teorema) ---------------------------------
    properties: list[Property] = []

    def add_theorem(prop_id: str, name: str, description: str, prop_lines: list[str]):
        start = len(lines) + 1  # 1-indexed line number of the FIRST line we add
        lines.extend(prop_lines)
        end = len(lines)
        properties.append(Property(prop_id, name, description, start, end))
        lines.append("")

    add_theorem(
        "P1",
        "prop1_subnetsWithinVpc",
        "Toda subnet esta contida dentro do CIDR da sua propria VPC.",
        [
            "theorem prop1_subnetsWithinVpc :",
            "    subnets.all subnetWithinItsVpc = true := by decide",
        ],
    )

    add_theorem(
        "P2",
        "prop2_noSubnetOverlap",
        "Nenhum par de subnets na mesma VPC tem faixas de IP sobrepostas.",
        [
            "theorem prop2_noSubnetOverlap :",
            "    subnets.all (fun a => subnets.all (fun b => subnetPairOk a b)) = true := by decide",
        ],
    )

    add_theorem(
        "P3",
        "prop3_noOpenSensitivePorts",
        f"Nenhum security group libera as portas {sensitive_ports} para 0.0.0.0/0.",
        [
            "theorem prop3_noOpenSensitivePorts :",
            "    secGroups.all sgIsSafe = true := by decide",
        ],
    )

    add_theorem(
        "P4",
        "prop4_referencesValid",
        "Toda instancia referencia uma subnet e security groups existentes.",
        [
            "theorem prop4_referencesValid :",
            "    instances.all instanceRefsOk = true := by decide",
        ],
    )

    return "\n".join(lines) + "\n", properties
