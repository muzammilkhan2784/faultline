/** A small typed fetch wrapper. Every backend error becomes an ApiError. */

export class ApiError extends Error {
  readonly status: number;
  readonly didYouMean: string[];

  constructor(status: number, message: string, didYouMean: string[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.didYouMean = didYouMean;
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

const BASE = "/api";

function url(path: string, params?: Params): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return `${BASE}${path}${suffix}`;
}

async function request<T>(path: string, init?: RequestInit, params?: Params): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url(path, params), {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Faultline API. Is it running on port 8000?");
  }

  if (response.status === 204) return undefined as T;

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const record = (body ?? {}) as { detail?: unknown; did_you_mean?: string[] };
    const detail =
      typeof record.detail === "string"
        ? record.detail
        : Array.isArray(record.detail)
          ? "The request was not valid."
          : response.statusText;
    throw new ApiError(response.status, detail, record.did_you_mean ?? []);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string, params?: Params) => request<T>(path, undefined, params),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  delete: (path: string) => request<void>(path, { method: "DELETE" }),
};
