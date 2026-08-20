/** Thin fetch wrapper: same-origin cookies, JSON errors, global 401 handling. */

export class ApiError extends Error {
  status: number;
  body: Record<string, unknown>;

  constructor(status: number, detail: string, body: Record<string, unknown> = {}) {
    super(detail);
    this.status = status;
    this.body = body;
  }
}

let onUnauthorized: () => void = () => {};

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

async function handle<T>(res: Response, skip401 = false): Promise<T> {
  if (res.status === 401 && !skip401) {
    onUnauthorized();
    throw new ApiError(401, "signed out");
  }
  if (!res.ok) {
    let body: Record<string, unknown> = {};
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      body = (await res.json()) as Record<string, unknown>;
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, body);
  }
  return (await res.json()) as T;
}

export async function apiGet<T>(path: string): Promise<T> {
  return handle<T>(await fetch(path, { credentials: "same-origin" }));
}

export async function apiSend<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: { skip401?: boolean } = {},
): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return handle<T>(res, opts.skip401);
}

export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(path, { method: "POST", credentials: "same-origin", body: form });
  return handle<T>(res);
}

export function rawUrl(vault: string, path: string): string {
  return `/api/vault/raw?vault=${encodeURIComponent(vault)}&path=${encodeURIComponent(path)}`;
}
