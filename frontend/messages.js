const messageBar = document.getElementById("message-bar");
const messageText = document.getElementById("message-text");

// Unico ponto de saida para mensagens de status/erro da interface -- nenhum
// outro elemento (badge, caixa inline) deve escrever texto de status.
function setMessage(kind, text) {
  messageBar.className = "message-bar message-" + kind;
  messageText.textContent = text;
}
