const form = document.getElementById("login-form");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const errorEl = document.getElementById("login-error");

function showError(message) {
  errorEl.hidden = false;
  errorEl.textContent = message;
}

function clearError() {
  errorEl.hidden = true;
  errorEl.textContent = "";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  const email = emailInput.value;
  const password = passwordInput.value;
  let response;
  try {
    response = await fetch("/api/v1/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ email, password }),
    });
  } catch {
    showError("Could not reach the local API.");
    return;
  }
  if (response.ok) {
    window.location.assign("/");
    return;
  }
  if (response.status === 401) {
    showError("Invalid email or password.");
    return;
  }
  if (response.status === 403) {
    showError("This sign-in request is not allowed.");
    return;
  }
  showError("Sign-in failed. Try again.");
});
