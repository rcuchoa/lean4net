const form = document.getElementById("login-form");
const submitBtn = document.getElementById("login-submit");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  submitBtn.disabled = true;
  setMessage("running", "Entrando...");

  const body = JSON.stringify({
    username: form.username.value,
    password: form.password.value,
    remember: form.remember.checked,
  });

  try {
    const res = await fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    const payload = await res.json();
    if (!res.ok) {
      setMessage("error", payload.error || "falha ao entrar");
      return;
    }
    setMessage("pass", "Login realizado. Redirecionando...");
    window.location.href = "/";
  } catch (err) {
    setMessage("error", `Falha ao conectar com o servidor: ${err.message}`);
  } finally {
    submitBtn.disabled = false;
  }
});
