import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { NotProvisionedError } from "../../api/client";
import { useMe } from "../../api/me";
import { CheckingAccess } from "./CheckingAccess";

/** The dashboard only renders after a successful /v1/me (UI-F2). */
export function AccessGate({ children }: { children: ReactNode }) {
  const me = useMe();
  if (me.isSuccess) return <>{children}</>;
  if (me.error instanceof NotProvisionedError) return <Navigate to="/no-access" replace />;
  if (me.isError) return <CheckingAccess failed onRetry={() => void me.refetch()} />;
  return <CheckingAccess onRetry={() => void me.refetch()} />;
}
