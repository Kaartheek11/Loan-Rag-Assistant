import { useAuth } from "../../auth/AuthContext";
import { Button } from "../../components/Button";
import { LogoMark } from "../../components/LogoMark";
import { brand } from "../../config/brand";

/** UI-F3: a valid Keycloak account with no LoanLens access. Sign out is the only action. */
export function NoAccessScreen() {
  const { signOut } = useAuth();
  return (
    <main className="flex min-h-full flex-col items-center justify-center gap-6 bg-paper p-6 text-center">
      <LogoMark size={48} />
      <h1 className="max-w-md font-display text-panel font-semibold text-ink">
        Your account is signed in, but it hasn&apos;t been set up for {brand.name} yet. Ask your
        administrator to add you.
      </h1>
      <Button onClick={signOut}>Sign out</Button>
    </main>
  );
}
