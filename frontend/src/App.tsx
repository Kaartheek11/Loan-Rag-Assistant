import { focusManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { ToastProvider } from "./components/Toast";
import { AccessGate } from "./features/access/AccessGate";
import { NoAccessScreen } from "./features/access/NoAccessScreen";
import { FilesPage } from "./features/admin/FilesPage";
import { ChatProvider } from "./features/chat/ChatContext";
import { ChatWindow } from "./features/chat/ChatWindow";
import { UploadQueueProvider } from "./features/admin/uploadQueue";
import { UploadTray } from "./features/admin/UploadTray";
import { DashboardLayout } from "./features/dashboard/DashboardLayout";
import { SourceProvider } from "./features/source/SourceContext";
import { WelcomeScreen } from "./features/welcome/WelcomeScreen";

// "When the window regains focus" (UI-F4, UI-G3): TanStack Query listens for tab visibility
// only; also treat returning to an already-visible window (a `focus` event) as focus.
focusManager.setEventListener((handleFocus) => {
  const onVisibility = () => handleFocus(document.visibilityState === "visible");
  const onFocus = () => handleFocus(true);
  window.addEventListener("visibilitychange", onVisibility, false);
  window.addEventListener("focus", onFocus, false);
  return () => {
    window.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("focus", onFocus);
  };
});

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } },
  });
}

/** Signed-in area: the access check, then chat and source panel state for this session. */
function SignedIn({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <AccessGate>
        <ChatProvider>
          <SourceProvider>
            <DashboardLayout>{children}</DashboardLayout>
          </SourceProvider>
        </ChatProvider>
        <UploadTray />
      </AccessGate>
    </RequireAuth>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<WelcomeScreen />} />
      <Route
        path="/dashboard"
        element={
          <SignedIn>
            <ChatWindow />
          </SignedIn>
        }
      />
      <Route
        path="/admin/files"
        element={
          <SignedIn>
            <FilesPage />
          </SignedIn>
        }
      />
      <Route
        path="/no-access"
        element={
          <RequireAuth>
            <NoAccessScreen />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App({ queryClient = makeQueryClient() }: { queryClient?: QueryClient }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <AuthProvider>
            <UploadQueueProvider>
              <AppRoutes />
            </UploadQueueProvider>
          </AuthProvider>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
