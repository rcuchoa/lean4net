const yamlInput = document.getElementById("yaml-input");
const leanOutput = document.getElementById("lean-output");
const promptOutput = document.getElementById("prompt-output");
const tabPrompt = document.getElementById("tab-prompt");
const tabLean = document.getElementById("tab-lean");
const resultOutput = document.getElementById("result-output");
const generateBtn = document.getElementById("generate-btn");
const verifyBtn = document.getElementById("verify-btn");
const fileInput = document.getElementById("file-input");

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// --- editor de propriedades customizadas ------------------------------------
//
// Vivem so no estado do navegador (nao ha banco de dados no projeto, nem
// persistencia entre reloads) e sao enviadas junto com o YAML/portas em
// cada requisicao -- ver `custom_properties` nos corpos de /api/prompt,
// /api/generate e /api/verify. O id (C1, C2, ...) e sempre gerado aqui,
// nunca digitado pelo usuario, para casar com o formato que o backend
// espera (`backend/core/spec_loader.load_custom_properties`).

const propertiesList = document.getElementById("properties-list");
const propertiesForm = document.getElementById("properties-form");
const propertiesInput = document.getElementById("properties-input");

let customProperties = [];
let customPropertyCounter = 0;

// As 4 propriedades originais do projeto pre-populam a lista (via
// /api/default-properties); a partir dai sao tratadas exatamente como
// qualquer outra -- o usuario pode edita-las, apaga-las ou adicionar mais.
// A zona 1 (Propriedades) e um pane proprio, sempre visivel -- sem toggle.
function renderCustomProperties() {
  propertiesList.innerHTML = customProperties
    .map(
      (p) => `
        <div class="property-row">
          <span class="property-id">${escapeHtml(p.id)}</span>
          <textarea class="property-desc-input" data-id="${escapeHtml(p.id)}" rows="1">${escapeHtml(p.description)}</textarea>
          <button type="button" class="property-remove" data-id="${escapeHtml(p.id)}" aria-label="Remover propriedade ${escapeHtml(p.id)}">&times;</button>
        </div>`
    )
    .join("");
}

propertiesForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const description = propertiesInput.value.trim();
  if (!description) return;
  customPropertyCounter += 1;
  customProperties.push({ id: `C${customPropertyCounter}`, description });
  propertiesInput.value = "";
  renderCustomProperties();
});

propertiesList.addEventListener("click", (e) => {
  const btn = e.target.closest(".property-remove");
  if (!btn) return;
  customProperties = customProperties.filter((p) => p.id !== btn.dataset.id);
  renderCustomProperties();
});

// Edicao in-place: atualiza o estado a cada tecla, sem re-renderizar a
// lista (o que perderia o foco/cursor do textarea sendo editado).
propertiesList.addEventListener("input", (e) => {
  const el = e.target.closest(".property-desc-input");
  if (!el) return;
  const prop = customProperties.find((p) => p.id === el.dataset.id);
  if (prop) prop.description = el.value;
});

const DEFAULT_PROPERTIES_FALLBACK = [
  {
    id: "C1",
    description:
      "Toda subnet esta contida dentro do CIDR da sua propria VPC (o CIDR da subnet esta contido no CIDR da VPC que ela referencia).",
  },
  {
    id: "C2",
    description:
      "Nenhum par de subnets distintas que pertencem a mesma VPC tem faixas de IP (CIDR) sobrepostas entre si.",
  },
  {
    id: "C3",
    description:
      "Nenhuma regra de ingress de nenhum security group cobre uma porta sensivel (22 e 3389 por padrao) e ao mesmo tempo libera a origem para o mundo todo (0.0.0.0/0).",
  },
  {
    id: "C4",
    description:
      "Toda instancia referencia, por nome, uma subnet que de fato existe na especificacao, e todos os security groups que ela referencia tambem existem na especificacao.",
  },
];

async function loadDefaultProperties() {
  let defaults;
  try {
    const res = await fetch("/api/default-properties");
    const data = await res.json();
    defaults = data.properties || [];
  } catch (e) {
    defaults = DEFAULT_PROPERTIES_FALLBACK;
  }
  customProperties = defaults.map((p) => ({ id: p.id, description: p.description }));
  customPropertyCounter = customProperties.reduce((max, p) => {
    const m = /^C(\d+)$/.exec(p.id);
    return m ? Math.max(max, parseInt(m[1], 10)) : max;
  }, 0);
  renderCustomProperties();
}

// A etapa 2 so faz sentido com algum Lean no painel; o botao acompanha o
// conteudo do textarea (que o usuario pode editar ou apagar a qualquer hora).
function syncVerifyEnabled() {
  verifyBtn.disabled = !leanOutput.value.trim();
}

// O painel do meio mostra duas visoes do mesmo passo: o prompt que vai para
// o modelo e o Lean que volta dele.
function showView(which) {
  const isPrompt = which === "prompt";
  promptOutput.hidden = !isPrompt;
  leanOutput.hidden = isPrompt;
  tabPrompt.classList.toggle("is-active", isPrompt);
  tabLean.classList.toggle("is-active", !isPrompt);
  tabPrompt.setAttribute("aria-selected", String(isPrompt));
  tabLean.setAttribute("aria-selected", String(!isPrompt));
}

// Texto exibido no painel: os parametros da requisicao e os dois prompts,
// exatamente como o servidor os enviara.
function formatPrompt(p) {
  const params = [
    `model:         ${p.model}`,
    `max_tokens:    ${p.max_tokens}`,
    `thinking:      ${JSON.stringify(p.thinking)}`,
    `output_config: ${JSON.stringify(p.output_config)}`,
  ].join("\n");

  return [
    "======== parametros da requisicao ========",
    params,
    "",
    "======== system prompt ========",
    p.system,
    "======== user prompt ========",
    p.user,
  ].join("\n");
}

async function loadExample() {
  try {
    const res = await fetch("/api/example");
    const data = await res.json();
    yamlInput.value = data.yaml || "";
  } catch (e) {
    yamlInput.value = "vpcs: []\nsubnets: []\nsecurity_groups: []\ninstances: []\n";
  }
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    yamlInput.value = reader.result;
  };
  reader.readAsText(file);
});

// Desenha so a identificacao estrutural de cada propriedade (icone/id/nome/
// descricao) -- nenhum texto de mensagem (nem do compilador Lean) e escrito
// aqui; todo o texto de erro/status, propriedade a propriedade, vai para a
// area de mensagens no rodape (ver setMessage / buildVerifyMessage).
function renderResults(report) {
  const rows = report.results
    .map((r) => {
      const icon = r.ok ? "✓" : "✗";
      const iconClass = r.ok ? "pass" : "fail";
      return `
        <div class="prop-row">
          <span class="prop-icon ${iconClass}">${icon}</span>
          <div class="prop-body">
            <div class="prop-title"><span class="prop-id">${escapeHtml(r.id)}</span>${escapeHtml(r.theorem_name)}</div>
            <div class="prop-desc">${escapeHtml(r.description)}</div>
          </div>
        </div>`;
    })
    .join("");

  resultOutput.innerHTML = rows;
}

// Junta a mensagem agregada com o texto de erro do compilador Lean de cada
// propriedade que falhou, para a area de mensagens do rodape ser a unica
// fonte desse texto (nunca aparece dentro do painel de resultado).
function buildVerifyMessage(report) {
  if (report.success) {
    return "Todas as propriedades foram verificadas pelo compilador Lean 4. QED.";
  }
  const failing = report.results.filter((r) => !r.ok && r.messages && r.messages.length);
  const header = "Uma ou mais propriedades nao puderam ser provadas.";
  if (!failing.length) return header;
  const details = failing
    .map((r) => `${r.id} ${r.theorem_name}:\n${r.messages.join("\n\n")}`)
    .join("\n\n");
  return `${header}\n\n${details}`;
}

// --- etapa 1: YAML -> Lean 4 (LLM) -----------------------------------------
//
// Duas requisicoes em sequencia: /api/prompt (instantanea, so monta o texto)
// para mostrar o prompt no painel, e depois /api/generate, que e a chamada
// cara. Assim o prompt fica visivel durante toda a espera da geracao -- e um
// YAML invalido e recusado ja na primeira, sem gastar chamada de API.

async function runGenerate() {
  const yamlText = yamlInput.value;
  if (!yamlText.trim()) return;

  const body = JSON.stringify({
    yaml: yamlText,
    custom_properties: customProperties,
  });

  generateBtn.disabled = true;
  verifyBtn.disabled = true;
  leanOutput.value = "";
  promptOutput.textContent = "";
  showView("prompt");
  setMessage("running", "Etapa 1/2 — montando o prompt...");

  try {
    const promptRes = await fetch("/api/prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    if (promptRes.status === 401) {
      window.location.href = "/login";
      return;
    }
    const promptPayload = await promptRes.json();

    if (promptPayload.error) {
      setMessage("error", promptPayload.error);
      return;
    }

    promptOutput.textContent = formatPrompt(promptPayload);
    setMessage("running", "Etapa 1/2 — gerando o Lean 4 via LLM...");

    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    const payload = await res.json();

    if (payload.error) {
      setMessage("error", payload.error);
      return;
    }

    leanOutput.value = payload.lean_source || "";
    const count = (payload.properties || []).length;
    showView("lean");
    setMessage("pass", `Lean 4 gerado (${count} propriedades) — revise e clique em "Verificar".`);
  } catch (e) {
    setMessage("error", `Falha ao conectar com o servidor: ${e.message}`);
  } finally {
    generateBtn.disabled = false;
    syncVerifyEnabled();
  }
}

// --- etapa 2: Lean 4 -> compilador -----------------------------------------

async function runVerify() {
  const leanText = leanOutput.value;
  if (!leanText.trim()) return;

  verifyBtn.disabled = true;
  showView("lean"); // o relatorio se refere a este texto -- deixe-o a vista
  setMessage("running", "Etapa 2/2 — executando o compilador Lean 4...");

  try {
    const res = await fetch("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lean_source: leanText, custom_properties: customProperties }),
    });
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    const payload = await res.json();

    if (payload.error) {
      setMessage("error", payload.error);
      return;
    }

    const report = payload.report;
    renderResults(report);
    setMessage(report.success ? "pass" : "fail", buildVerifyMessage(report));
  } catch (e) {
    setMessage("error", `Falha ao conectar com o servidor: ${e.message}`);
  } finally {
    syncVerifyEnabled();
  }
}

generateBtn.addEventListener("click", runGenerate);
verifyBtn.addEventListener("click", runVerify);
leanOutput.addEventListener("input", syncVerifyEnabled);
tabPrompt.addEventListener("click", () => showView("prompt"));
tabLean.addEventListener("click", () => showView("lean"));
loadExample();
loadDefaultProperties();

// --- sessao (usuario logado no header) --------------------------------------

async function loadSession() {
  try {
    const res = await fetch("/api/whoami");
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    const data = await res.json();
    document.getElementById("session-user").textContent = data.username || "";
  } catch (e) {
    // sem sessao visivel nao bloqueia o uso da pagina em si
  }
}
loadSession();

// --- areas redimensionaveis ------------------------------------------------

const mainGrid = document.getElementById("main-grid");
const vResizer = document.getElementById("v-resizer");
const columnLeft = document.getElementById("column-left");
const columnRight = document.getElementById("column-right");
const hResizerLeft = document.getElementById("h-resizer-left");
const hResizerRight = document.getElementById("h-resizer-right");

const DEFAULT_COLUMNS = "1fr 6px 1fr";
const DEFAULT_ROWS = "minmax(150px, 1fr) 6px minmax(150px, 1fr)";
const MOBILE_QUERY = window.matchMedia("(max-width: 800px)");
const MIN_PANE_PX = 160;

function clamp(v, min, max) {
  return Math.min(Math.max(v, min), max);
}

function makeResizer(handle, { onDrag, onReset }) {
  handle.addEventListener("pointerdown", (e) => {
    if (MOBILE_QUERY.matches) return;
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
  });
  handle.addEventListener("pointermove", (e) => {
    if (!handle.hasPointerCapture(e.pointerId)) return;
    onDrag(e);
  });
  handle.addEventListener("pointerup", (e) => {
    if (handle.hasPointerCapture(e.pointerId)) {
      handle.releasePointerCapture(e.pointerId);
    }
    handle.classList.remove("dragging");
  });
  handle.addEventListener("dblclick", onReset);
}

// Divisor unico entre a coluna esquerda (zonas 1+2) e a direita (zonas 3+4).
makeResizer(vResizer, {
  onDrag: (e) => {
    const rect = mainGrid.getBoundingClientRect();
    const x = clamp(e.clientX - rect.left, MIN_PANE_PX, rect.width - MIN_PANE_PX - 6);
    mainGrid.style.gridTemplateColumns = `${x}px 6px ${rect.width - x - 6}px`;
  },
  onReset: () => {
    mainGrid.style.gridTemplateColumns = DEFAULT_COLUMNS;
  },
});

// Cada coluna tem seu proprio divisor horizontal, independente da outra
// (zona 1/2 na esquerda, zona 3/4 na direita nao precisam ter a mesma altura).
function makeColumnResizer(handle, column) {
  makeResizer(handle, {
    onDrag: (e) => {
      const rect = column.getBoundingClientRect();
      const y = clamp(e.clientY - rect.top, MIN_PANE_PX, rect.height - MIN_PANE_PX - 6);
      column.style.gridTemplateRows = `${y}px 6px ${rect.height - y - 6}px`;
    },
    onReset: () => {
      column.style.gridTemplateRows = DEFAULT_ROWS;
    },
  });
}
makeColumnResizer(hResizerLeft, columnLeft);
makeColumnResizer(hResizerRight, columnRight);

function syncGridForViewport() {
  if (MOBILE_QUERY.matches) {
    mainGrid.style.gridTemplateColumns = "";
    columnLeft.style.gridTemplateRows = "";
    columnRight.style.gridTemplateRows = "";
  }
}
MOBILE_QUERY.addEventListener("change", syncGridForViewport);
syncGridForViewport();
