// frontend/src/lib/api.ts
export async function api(path: string, init?: RequestInit) {
  const url = `/api/${path.replace(/^\/+/, '')}`;
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json().catch(() => ({}));
}
