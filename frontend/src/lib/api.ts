// src/lib/api.ts
const BASE = (process.env.NEXT_PUBLIC_API_BASE || '').replace(/\/+$/, '');

export async function api(path: string, init?: RequestInit) {
  if (!BASE) throw new Error('Missing NEXT_PUBLIC_API_BASE');
  const url = `${BASE}/${path.replace(/^\/+/, '')}`;
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json().catch(() => ({}));
}
