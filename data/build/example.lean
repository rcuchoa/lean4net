structure Cidr where
  addr : Nat
  plen : Nat
  deriving DecidableEq, Repr

structure Rule where
  proto : String
  portFrom : Nat
  portTo : Nat
  srcCidr : Cidr
  deriving Repr

structure Vpc where
  name : String
  cidr : Cidr
  deriving Repr

structure Subnet where
  name : String
  vpc : String
  cidr : Cidr
  deriving Repr

structure SecGroup where
  name : String
  rules : List Rule
  deriving Repr

structure Instance where
  name : String
  subnet : String
  sgs : List String
  deriving Repr

/-- Menor dos dois naturais (evita dependencias extras). -/
def natMin (a b : Nat) : Nat := if a ≤ b then a else b

/-- Mascara de rede de 32 bits para o prefixo `p`. -/
def netMask (p : Nat) : Nat := (2 ^ 32 - 1) - (2 ^ (32 - p) - 1)

/-- Endereco de rede de um CIDR (endereco AND mascara do proprio prefixo). -/
def netAddr (c : Cidr) : Nat := Nat.land c.addr (netMask c.plen)

/-- `outer` contem `inner`. -/
def cidrContains (outer inner : Cidr) : Bool :=
  (outer.plen ≤ inner.plen) &&
    (Nat.land inner.addr (netMask outer.plen) == netAddr outer)

/-- Os dois CIDRs se sobrepoem. -/
def cidrOverlaps (a b : Cidr) : Bool :=
  let p := natMin a.plen b.plen
  Nat.land a.addr (netMask p) == Nat.land b.addr (netMask p)

/-- A regra cobre a porta dada. -/
def coversPort (r : Rule) (port : Nat) : Bool :=
  (r.portFrom ≤ port) && (port ≤ r.portTo)

/-- A regra libera a origem para o mundo todo (0.0.0.0/0). -/
def isOpenToWorld (r : Rule) : Bool := r.srcCidr.plen == 0

def vpcs : List Vpc :=
  [ { name := "main-vpc", cidr := { addr := 167772160, plen := 16 } } ]

def subnets : List Subnet :=
  [ { name := "public-subnet",  vpc := "main-vpc", cidr := { addr := 167772416, plen := 24 } },
    { name := "private-subnet", vpc := "main-vpc", cidr := { addr := 167772672, plen := 24 } } ]

def secGroups : List SecGroup :=
  [ { name := "web-sg",
      rules :=
        [ { proto := "tcp", portFrom := 80,  portTo := 80,  srcCidr := { addr := 0, plen := 0 } },
          { proto := "tcp", portFrom := 443, portTo := 443, srcCidr := { addr := 0, plen := 0 } } ] },
    { name := "ssh-sg",
      rules :=
        [ { proto := "tcp", portFrom := 22, portTo := 22, srcCidr := { addr := 167772160, plen := 16 } } ] },
    { name := "db-sg",
      rules :=
        [ { proto := "tcp", portFrom := 5432, portTo := 5432, srcCidr := { addr := 167772672, plen := 24 } } ] } ]

def instances : List Instance :=
  [ { name := "web1", subnet := "public-subnet",  sgs := ["web-sg", "ssh-sg"] },
    { name := "db1",  subnet := "private-subnet", sgs := ["db-sg"] } ]

def sensitivePorts : List Nat := [22, 3389]

/-- Busca a VPC pelo nome. -/
def findVpc (n : String) : Option Vpc := vpcs.find? (fun v => v.name == n)

/-- A subnet esta contida no CIDR da VPC que ela referencia. -/
def subnetWithinItsVpc (s : Subnet) : Bool :=
  match findVpc s.vpc with
  | some v => cidrContains v.cidr s.cidr
  | none => false

/-- Par de subnets aceitavel: iguais, de VPCs diferentes, ou sem sobreposicao. -/
def subnetPairOk (a b : Subnet) : Bool :=
  if a.name == b.name then true
  else if a.vpc == b.vpc then !(cidrOverlaps a.cidr b.cidr)
  else true

/-- A regra expoe uma porta sensivel para o mundo todo. -/
def ruleExposesSensitivePort (r : Rule) : Bool :=
  isOpenToWorld r && sensitivePorts.any (fun p => coversPort r p)

/-- Nenhuma regra do security group expoe porta sensivel ao mundo. -/
def sgIsSafe (g : SecGroup) : Bool :=
  g.rules.all (fun r => !(ruleExposesSensitivePort r))

def subnetExists (n : String) : Bool := subnets.any (fun s => s.name == n)

def sgExists (n : String) : Bool := secGroups.any (fun g => g.name == n)

/-- A instancia referencia subnet e security groups existentes. -/
def instanceRefsOk (i : Instance) : Bool :=
  subnetExists i.subnet && i.sgs.all sgExists

-- @property:P1
theorem prop1_subnetsWithinVpc :
    subnets.all subnetWithinItsVpc = true := by decide
-- @end_property:P1

-- @property:P2
theorem prop2_noSubnetOverlap :
    subnets.all (fun a => subnets.all (fun b => subnetPairOk a b)) = true := by decide
-- @end_property:P2

-- @property:P3
theorem prop3_noOpenSensitivePorts :
    secGroups.all sgIsSafe = true := by decide
-- @end_property:P3

-- @property:P4
theorem prop4_referencesValid :
    instances.all instanceRefsOk = true := by decide
-- @end_property:P4
