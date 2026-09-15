# lean4net — verificador formal de infraestrutura de nuvem

Recebe uma especificação simples de infraestrutura de nuvem virtual (YAML),
gera automaticamente um programa em **Lean 4** que modela essa
infraestrutura e formula um conjunto de propriedades como proposições
*decidíveis*, e usa o próprio **compilador Lean** para validar
matematicamente se as propriedades são verdadeiras.

Se o Lean aceita o arquivo gerado (exit code 0), cada propriedade foi
verificada — não testada em alguns casos, mas provada para todos os
valores da especificação, porque o kernel do Lean avaliou a função
booleana correspondente sobre os dados exatos da spec e conferiu que o
resultado é `true`.

## Como funciona (pipeline)

```
spec/*.yaml  --[spec_loader.py]-->  modelo Python (CIDRs já parseados)
             --[lean_codegen.py]--> build/*.lean  (dados + teoremas `by decide`)
             --[runner.py]-------->  `lean build/*.lean`  (compilador Lean 4)
             --[main.py]---------->  relatório PASS/FAIL por propriedade
```

- **`verifier/model.py`** — dataclasses da infraestrutura (Vpc, Subnet,
  SecurityGroup, Rule, Instance) e parsing de CIDR (`"10.0.1.0/24"` →
  inteiro de 32 bits + tamanho de prefixo).
- **`verifier/spec_loader.py`** — lê o YAML e valida a estrutura (campos
  obrigatórios, CIDRs bem formados, nomes únicos) antes de gerar qualquer
  coisa.
- **`verifier/lean_codegen.py`** — gera um `.lean` autocontido: uma
  pequena biblioteca de aritmética de CIDR (máscara de rede, contenção,
  sobreposição, checagem de porta) + os dados da spec como termos Lean +
  um `theorem ... := by decide` por propriedade. Também devolve o
  intervalo de linhas de cada teorema, para depois ligar erros do
  compilador de volta à propriedade correspondente.
- **`verifier/runner.py`** — chama o binário `lean` sobre o arquivo
  gerado e faz o parsing das mensagens `arquivo:linha:coluna: error: ...`
  para descobrir qual propriedade falhou.
- **`main.py`** — orquestra tudo e imprime o relatório.

## Propriedades verificadas

| ID | Nome                        | Significado |
|----|------------------------------|-------------|
| P1 | `subnetsWithinVpc`           | Toda subnet está contida no CIDR da sua própria VPC. |
| P2 | `noSubnetOverlap`             | Nenhum par de subnets da mesma VPC tem faixas de IP sobrepostas. |
| P3 | `noOpenSensitivePorts`        | Nenhum security group libera portas sensíveis (22, 3389 por padrão) para `0.0.0.0/0`. |
| P4 | `referencesValid`             | Toda instância referencia uma subnet e security groups que de fato existem na spec. |

O veredito é **por propriedade** (não tudo-ou-nada): se só uma propriedade
falhar, as demais ainda são reportadas como provadas independentemente,
porque cada uma é um `theorem` Lean separado.

## Uso

Pré-requisito: Lean 4 instalado via [elan](https://github.com/leanprover/elan)
(`lean --version` deve funcionar). Nenhuma dependência de Mathlib é
necessária — só `Init`, que já vem com o toolchain.

```bash
pip install pyyaml   # já costuma vir instalado

python3 main.py spec/example.yaml           # tudo passa
python3 main.py spec/example_bad.yaml       # tudo falha (violações propositais)
python3 main.py spec/example_partial.yaml   # só a P3 falha (SSH aberto ao mundo)
```

Opções:

```bash
python3 main.py spec/example.yaml \
  --out build/minha_infra.lean \
  --sensitive-ports 22,3389,3306 \
  --lean-bin /caminho/para/lean
```

O `.lean` gerado fica em `build/` e pode ser aberto/inspecionado
normalmente (ou aberto no VS Code com a extensão do Lean 4 para ver os
teoremas provados interativamente).

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

## Extensão

Para adicionar uma nova propriedade: escreva a função booleana em
`_PRELUDE` (ou como auxiliar no `generate()`) em `lean_codegen.py`,
adicione um `add_theorem(...)` e pronto — o restante do pipeline
(geração, execução, parsing de erro por linha) já é genérico.

Para infraestruturas maiores (centenas de subnets/regras), troque `by
decide` por `by native_decide` em `lean_codegen.py` — troca prova por
avaliação no kernel por avaliação via código nativo compilado, muito mais
rápida, ao custo de confiar também no compilador Lean para código nativo
(não só no kernel).
