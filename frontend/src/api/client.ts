// Fetch wrapper (extension.md §10.2): token refresh before each call, Bearer header,
// one refresh-and-retry on 401, and error mapping. Tokens never leave keycloak-js memory.
import { env } from "../config/env";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | null,
    readonly requestId: string | null,
    readonly body: unknown = null,
  ) {
    super(`API error ${status}`);
  }
}

export class NetworkError extends Error {
  constructor() {
    super("network error");
  }
}

/** The backend rejected a fresh token: account not provisioned or disabled. */
export class NotProvisionedError extends ApiError {}

export type TokenSource = {
  /** Return a token valid for at least `minValidity` seconds; -1 forces a refresh. */
  getToken(minValidity: number): Promise<string>;
  /** Called when the session cannot be refreshed: clear state and sign in again. */
  onSessionEnded(): void;
};

export type RequestOptions = RequestInit & {
  /** "no-access" (for /v1/me): a 401 after a successful refresh means "not provisioned". */
  on401?: "session" | "no-access";
};

export class ApiClient {
  constructor(
    private readonly tokens: TokenSource,
    private readonly baseUrl: string = env.apiBase,
    private readonly fetchImpl: typeof fetch = (...args) => fetch(...args),
  ) {}

  async bearer(minValidity = 30): Promise<string> {
    try {
      return await this.tokens.getToken(minValidity);
    } catch {
      this.tokens.onSessionEnded();
      throw new ApiError(401, "unauthorized", null);
    }
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { on401 = "session", ...init } = options;
    let response = await this.send(path, init, await this.bearer(30));
    if (response.status === 401) {
      // Refresh once and retry once (never more).
      const fresh = await this.bearer(-1);
      response = await this.send(path, init, fresh);
      if (response.status === 401) {
        if (on401 === "no-access")
          throw new NotProvisionedError(401, "unauthorized", requestIdOf(response));
        this.tokens.onSessionEnded();
        throw new ApiError(401, "unauthorized", requestIdOf(response));
      }
    }
    const body = await readJson(response);
    if (!response.ok) {
      const code = body && typeof body === "object" && "error" in body ? String(body.error) : null;
      throw new ApiError(response.status, code, requestIdOf(response), body);
    }
    return body as T;
  }

  private async send(path: string, init: RequestInit, token: string): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !(init.body instanceof FormData))
      headers.set("Content-Type", "application/json");
    try {
      return await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        headers,
        cache: "no-store",
      });
    } catch {
      throw new NetworkError();
    }
  }
}

function requestIdOf(response: Response): string | null {
  return response.headers.get("X-Request-Id");
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
