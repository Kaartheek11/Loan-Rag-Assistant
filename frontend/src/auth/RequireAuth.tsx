import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { CheckingAccess } from "../features/access/CheckingAccess";
import { useAuth } from "./AuthContext";

/** Route guard for navigation and display only; the backend checks every call (§2). */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "loading") return <CheckingAccess phase="signing-in" />;
  if (status !== "authenticated") return <Navigate to="/" replace />;
  return <>{children}</>;
}
