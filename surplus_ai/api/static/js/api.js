const PAGE_SIZE = 50;

function operatorMessage(status) {
  if (status === 401) {
    return "Authentication is required.";
  }
  if (status === 404) {
    return "The requested record was not found.";
  }
  if (status === 422) {
    return "The request was invalid.";
  }
  if (status === 500) {
    return "The API reported an internal error.";
  }
  if (status === 0) {
    return "The API is unavailable.";
  }
  return "Could not load data.";
}

function redirectToLogin() {
  window.location.assign("/login");
}

export async function apiGet(path) {
  let response;
  try {
    response = await fetch(path, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
  } catch {
    const error = new Error(operatorMessage(0));
    error.status = 0;
    throw error;
  }

  if (response.status === 401) {
    redirectToLogin();
    const error = new Error(operatorMessage(401));
    error.status = 401;
    throw error;
  }

  if (!response.ok) {
    const error = new Error(operatorMessage(response.status));
    error.status = response.status;
    throw error;
  }

  return response.json();
}

export async function apiPost(path) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
  } catch {
    const error = new Error(operatorMessage(0));
    error.status = 0;
    throw error;
  }

  if (response.status === 401) {
    redirectToLogin();
    const error = new Error(operatorMessage(401));
    error.status = 401;
    throw error;
  }

  if (!response.ok) {
    const error = new Error(operatorMessage(response.status));
    error.status = response.status;
    throw error;
  }

  if (response.status === 204) {
    return null;
  }
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

export function listUrl(resourcePath, offset) {
  const params = new URLSearchParams();
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(offset));
  return `${resourcePath}?${params.toString()}`;
}

export { PAGE_SIZE };
