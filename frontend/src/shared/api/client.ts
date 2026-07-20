/** Thrown by `apiGet` for any non-2xx response. There is no documented error response shape
 *  beyond a generic 422 for structurally invalid query params, so this treats every non-2xx
 *  response generically: it best-effort parses the body as JSON and attaches it as-is. */
export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly body: unknown;

  constructor(status: number, statusText: string, body: unknown) {
    super(`API request failed with status ${status} ${statusText}`);
    this.name = "ApiError";
    this.status = status;
    this.statusText = statusText;
    this.body = body;
  }
}

export type QueryParamValue = string | number | undefined;

export interface ApiGetOptions {
  params?: Record<string, QueryParamValue>;
}

// Empty string is a deliberate, valid configuration (see vite.config.ts / README.md): it makes
// the client issue relative requests that the Vite dev/preview proxy forwards to the backend,
// avoiding browser CORS. `??` only falls back on null/undefined, never on "", so this is safe.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function toQueryString(params: Record<string, QueryParamValue>): string {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) {
      searchParams.set(key, String(value));
    }
  }
  const serialized = searchParams.toString();
  return serialized ? `?${serialized}` : "";
}

async function safeParseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

/** Typed GET wrapper: resolves with the parsed JSON body, or rejects with an `ApiError` for any
 *  non-2xx response. */
export async function apiGet<T>(path: string, options: ApiGetOptions = {}): Promise<T> {
  const query = options.params ? toQueryString(options.params) : "";
  const response = await fetch(`${BASE_URL}${path}${query}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    const body = await safeParseJson(response);
    throw new ApiError(response.status, response.statusText, body);
  }

  return (await response.json()) as T;
}
