import { describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError, NetworkError, NotProvisionedError } from "../../src/api/client";

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("ApiClient", () => {
  it("refreshes the token (30 s window) before every request and sends it", async () => {
    const getToken = vi.fn().mockResolvedValue("tok-1");
    const fetchImpl = vi.fn().mockResolvedValue(json(200, { ok: true }));
    const api = new ApiClient({ getToken, onSessionEnded: vi.fn() }, "/v1", fetchImpl);
    await api.request("/me");
    expect(getToken).toHaveBeenCalledWith(30);
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/v1/me");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok-1");
  });

  it("on 401 refreshes once and retries once", async () => {
    const getToken = vi.fn().mockResolvedValueOnce("old").mockResolvedValueOnce("new");
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(json(401, { error: "unauthorized" }))
      .mockResolvedValueOnce(json(200, { ok: true }));
    const onSessionEnded = vi.fn();
    const api = new ApiClient({ getToken, onSessionEnded }, "/v1", fetchImpl);
    await expect(api.request("/query")).resolves.toEqual({ ok: true });
    expect(getToken).toHaveBeenNthCalledWith(2, -1); // forced refresh
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(onSessionEnded).not.toHaveBeenCalled();
  });

  it("never retries a 401 more than once, then triggers sign-in", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(401, { error: "unauthorized" }));
    const onSessionEnded = vi.fn();
    const api = new ApiClient(
      { getToken: vi.fn().mockResolvedValue("t"), onSessionEnded },
      "/v1",
      fetchImpl,
    );
    await expect(api.request("/query")).rejects.toBeInstanceOf(ApiError);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(onSessionEnded).toHaveBeenCalledTimes(1);
  });

  it("triggers sign-in when the token cannot be refreshed", async () => {
    const onSessionEnded = vi.fn();
    const api = new ApiClient(
      { getToken: vi.fn().mockRejectedValue(new Error("expired")), onSessionEnded },
      "/v1",
      vi.fn(),
    );
    await expect(api.request("/me")).rejects.toMatchObject({ status: 401 });
    expect(onSessionEnded).toHaveBeenCalled();
  });

  it("maps a 401 on /v1/me after a good refresh to not-provisioned, without sign-in", async () => {
    const onSessionEnded = vi.fn();
    const api = new ApiClient(
      { getToken: vi.fn().mockResolvedValue("t"), onSessionEnded },
      "/v1",
      vi.fn().mockResolvedValue(json(401, { error: "unauthorized" })),
    );
    await expect(api.request("/me", { on401: "no-access" })).rejects.toBeInstanceOf(
      NotProvisionedError,
    );
    expect(onSessionEnded).not.toHaveBeenCalled();
  });

  it("maps errors with code and request id, and network failures", async () => {
    const api = new ApiClient(
      { getToken: vi.fn().mockResolvedValue("t"), onSessionEnded: vi.fn() },
      "/v1",
      vi.fn().mockResolvedValue(json(429, { error: "rate_limited" }, { "X-Request-Id": "rid-9" })),
    );
    await expect(api.request("/query")).rejects.toMatchObject({
      status: 429,
      code: "rate_limited",
      requestId: "rid-9",
    });
    const down = new ApiClient(
      { getToken: vi.fn().mockResolvedValue("t"), onSessionEnded: vi.fn() },
      "/v1",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    await expect(down.request("/query")).rejects.toBeInstanceOf(NetworkError);
  });
});
