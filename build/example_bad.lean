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

def vpcs : List Vpc := [
  { name := "main-vpc", cidr := { addr := 167772160, plen := 16 } },
]

def subnets : List Subnet := [
  { name := "public-subnet", vpc := "main-vpc", cidr := { addr := 167772416, plen := 24 } },
  { name := "extra-subnet", vpc := "main-vpc", cidr := { addr := 167772544, plen := 25 } },
  { name := "outside-subnet", vpc := "main-vpc", cidr := { addr := 3232235776, plen := 24 } },
]

def secGroups : List SecGroup := [
  { name := "web-sg", rules := [{ proto := "tcp", portFrom := 80, portTo := 80, srcCidr := { addr := 0, plen := 0 } }] },
  { name := "ssh-sg", rules := [{ proto := "tcp", portFrom := 22, portTo := 22, srcCidr := { addr := 0, plen := 0 } }] },
]

def instances : List Instance := [
  { name := "web1", subnet := "public-subnet", sgs := ["web-sg", "ssh-sg"] },
  { name := "web2", subnet := "public-subnet", sgs := ["web-sg2"] },
]

def sensitivePorts : List Nat := [22, 3389]

def findVpc (name : String) : Option Vpc :=
  vpcs.find? (fun v => v.name == name)

def subnetWithinItsVpc (s : Subnet) : Bool :=
  match findVpc s.vpc with
  | some v => cidrContains v.cidr s.cidr
  | none => false

def subnetPairOk (a b : Subnet) : Bool :=
  if a.name == b.name then true
  else if a.vpc == b.vpc then !(cidrOverlaps a.cidr b.cidr)
  else true

def ruleExposesSensitivePort (r : Rule) : Bool :=
  isOpenToWorld r && sensitivePorts.any (coversPort r)

def sgIsSafe (sg : SecGroup) : Bool :=
  sg.rules.all (fun r => !(ruleExposesSensitivePort r))

def subnetExists (name : String) : Bool := subnets.any (fun s => s.name == name)
def sgExists (name : String) : Bool := secGroups.any (fun sg => sg.name == name)

def instanceRefsOk (i : Instance) : Bool :=
  subnetExists i.subnet && i.sgs.all sgExists

theorem prop1_subnetsWithinVpc :
    subnets.all subnetWithinItsVpc = true := by decide

theorem prop2_noSubnetOverlap :
    subnets.all (fun a => subnets.all (fun b => subnetPairOk a b)) = true := by decide

theorem prop3_noOpenSensitivePorts :
    secGroups.all sgIsSafe = true := by decide

theorem prop4_referencesValid :
    instances.all instanceRefsOk = true := by decide

