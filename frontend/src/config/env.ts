// Public build-time settings only (UI-S5): no secrets ever reach the bundle.
export const env = {
  keycloakUrl: import.meta.env.VITE_KEYCLOAK_URL ?? "/auth",
  keycloakRealm: import.meta.env.VITE_KEYCLOAK_REALM ?? "loanrag",
  keycloakClientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "loanrag-web",
  apiBase: import.meta.env.VITE_API_BASE ?? "/v1",
} as const;
