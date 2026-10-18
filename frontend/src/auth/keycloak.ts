import Keycloak from "keycloak-js";

import { env } from "../config/env";

// One instance for the app. Tokens stay in keycloak-js memory only (UI-S3).
export const keycloak = new Keycloak({
  url: new URL(env.keycloakUrl, window.location.origin).toString().replace(/\/$/, ""),
  realm: env.keycloakRealm,
  clientId: env.keycloakClientId,
});

let initPromise: Promise<boolean> | null = null;

export function initKeycloak(): Promise<boolean> {
  initPromise ??= keycloak.init({
    onLoad: "check-sso",
    pkceMethod: "S256",
    silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
    checkLoginIframe: false,
  });
  return initPromise;
}
