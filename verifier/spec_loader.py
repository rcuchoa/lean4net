"""Carrega e valida (estruturalmente) uma especificacao YAML de infraestrutura.

Validacoes feitas aqui (rapidas, em Python, antes de gerar Lean):
  - chaves obrigatorias presentes
  - CIDRs bem formados
  - nomes unicos dentro de cada categoria
  - referencias sintaticas presentes (nao valida aqui se apontam para algo
    existente -- isso e uma das PROPRIEDADES verificadas pelo Lean, ver
    verifier/lean_codegen.py, propriedade P4)
"""

from __future__ import annotations

import yaml

from verifier.model import Cidr, Instance, InfraSpec, Rule, SecurityGroup, Subnet, Vpc, SpecError


def _require(d: dict, key: str, where: str):
    if key not in d:
        raise SpecError(f"{where}: campo obrigatorio ausente: {key!r}")
    return d[key]


def _check_unique(names: list[str], kind: str):
    seen = set()
    for n in names:
        if n in seen:
            raise SpecError(f"{kind}: nome duplicado {n!r}")
        seen.add(n)


def load_spec(path: str) -> InfraSpec:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise SpecError("documento YAML raiz deve ser um mapeamento")

    vpcs = []
    for v in raw.get("vpcs", []) or []:
        name = _require(v, "name", "vpc")
        cidr_text = _require(v, "cidr", f"vpc {name!r}")
        vpcs.append(Vpc(name=name, cidr=Cidr.parse(cidr_text, f"vpc {name!r}")))
    _check_unique([v.name for v in vpcs], "vpcs")

    subnets = []
    for s in raw.get("subnets", []) or []:
        name = _require(s, "name", "subnet")
        vpc_ref = _require(s, "vpc", f"subnet {name!r}")
        cidr_text = _require(s, "cidr", f"subnet {name!r}")
        subnets.append(
            Subnet(name=name, vpc=vpc_ref, cidr=Cidr.parse(cidr_text, f"subnet {name!r}"))
        )
    _check_unique([s.name for s in subnets], "subnets")

    sgs = []
    for sg in raw.get("security_groups", []) or []:
        name = _require(sg, "name", "security_group")
        rules = []
        for r in sg.get("ingress", []) or []:
            proto = r.get("proto", "tcp")
            port_from = _require(r, "port_from", f"security_group {name!r} ingress")
            port_to = r.get("port_to", port_from)
            src_text = _require(r, "src", f"security_group {name!r} ingress")
            rules.append(
                Rule(
                    proto=str(proto),
                    port_from=int(port_from),
                    port_to=int(port_to),
                    src=Cidr.parse(src_text, f"security_group {name!r} ingress"),
                )
            )
        sgs.append(SecurityGroup(name=name, rules=rules))
    _check_unique([sg.name for sg in sgs], "security_groups")

    instances = []
    for i in raw.get("instances", []) or []:
        name = _require(i, "name", "instance")
        subnet_ref = _require(i, "subnet", f"instance {name!r}")
        sg_refs = i.get("security_groups", []) or []
        instances.append(
            Instance(name=name, subnet=subnet_ref, security_groups=list(sg_refs))
        )
    _check_unique([i.name for i in instances], "instances")

    return InfraSpec(vpcs=vpcs, subnets=subnets, security_groups=sgs, instances=instances)
