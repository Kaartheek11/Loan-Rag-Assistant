import * as Dialog from "@radix-ui/react-dialog";
import { Menu, PanelLeftOpen } from "lucide-react";
import { type ReactNode, useState } from "react";

import { LogoMark } from "../../components/LogoMark";
import { brand } from "../../config/brand";
import { useBreakpoint } from "../../lib/useBreakpoint";
import { Sidebar } from "../sidebar/Sidebar";
import { SourcePanel } from "../source/SourcePanel";

/** Desktop: sidebar | content | source panel. Tablet: icon rail that expands as an overlay.
 *  Phone: header button opens the sidebar as a drawer (extension.md §8.5). */
export function DashboardLayout({ children }: { children: ReactNode }) {
  const bp = useBreakpoint();
  const [drawer, setDrawer] = useState(false);

  const drawerSidebar = (
    <Dialog.Root open={drawer} onOpenChange={setDrawer}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content className="fixed inset-y-0 left-0 z-50 w-[300px] max-w-[85vw] shadow-2xl">
          <Dialog.Title className="sr-only">Menu</Dialog.Title>
          <Dialog.Description className="sr-only">
            Your access, files, and settings
          </Dialog.Description>
          <Sidebar onNavigate={() => setDrawer(false)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );

  if (bp === "phone") {
    return (
      <div className="flex h-full flex-col">
        <header className="flex items-center gap-3 bg-spruce-deep px-4 py-3 text-on-spruce-deep">
          <button
            type="button"
            aria-label="Open menu"
            onClick={() => setDrawer(true)}
            className="rounded-lg p-1.5 hover:bg-white/10"
          >
            <Menu size={20} aria-hidden="true" />
          </button>
          <LogoMark size={24} />
          <span className="font-display font-semibold">{brand.name}</span>
        </header>
        <main className="min-h-0 flex-1">{children}</main>
        {drawerSidebar}
        <SourcePanel breakpoint={bp} />
      </div>
    );
  }

  if (bp === "tablet") {
    return (
      <div className="flex h-full">
        <nav
          aria-label="Sidebar rail"
          className="flex w-16 shrink-0 flex-col items-center gap-4 bg-spruce-deep py-4 text-on-spruce-deep"
        >
          <LogoMark size={28} />
          <button
            type="button"
            aria-label="Open menu"
            onClick={() => setDrawer(true)}
            className="rounded-lg p-2 hover:bg-white/10"
          >
            <PanelLeftOpen size={20} aria-hidden="true" />
          </button>
        </nav>
        <main className="min-w-0 flex-1">{children}</main>
        {drawerSidebar}
        <SourcePanel breakpoint={bp} />
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <div className="w-[280px] shrink-0">
        <Sidebar />
      </div>
      <main className="min-w-0 flex-1">{children}</main>
      <SourcePanel breakpoint={bp} />
    </div>
  );
}
