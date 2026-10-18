import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import { AuthContext, type AuthValue } from "../../src/auth/AuthContext";
import { ToastProvider } from "../../src/components/Toast";
import { ChatProvider } from "../../src/features/chat/ChatContext";
import { SourceProvider } from "../../src/features/source/SourceContext";

export const API = "http://localhost/v1";

export function renderApp(ui: ReactNode, { route = "/dashboard" } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const auth: AuthValue = {
    status: "authenticated",
    api: new ApiClient({ getToken: async () => "test-token", onSessionEnded: vi.fn() }, API),
    getToken: async () => "test-token",
    signIn: vi.fn(),
    signOut: vi.fn(),
  };
  const result = render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={[route]}>
          <AuthContext.Provider value={auth}>
            <ChatProvider>
              <SourceProvider>{ui}</SourceProvider>
            </ChatProvider>
          </AuthContext.Provider>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { ...result, queryClient, auth };
}
