import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Monitor, Moon, Sun } from "lucide-react";
import { useState } from "react";

type Theme = "light" | "dark" | "system";

function current(): Theme {
  const t = document.documentElement.dataset.theme;
  return t === "light" || t === "dark" ? t : "system";
}

/** Light, dark, or system (§8.5). Held in memory only: nothing is written to browser storage. */
export function applyTheme(theme: Theme) {
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

const options: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System", Icon: Monitor },
];

export function ThemeToggle({ className = "" }: { className?: string }) {
  const [theme, setTheme] = useState<Theme>(current);
  const active = options.find((o) => o.value === theme) ?? options[2]!;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={`Theme: ${active.label}`}
        className={`inline-flex items-center gap-2 rounded-xl px-3 py-2 text-ui ${className}`}
      >
        <active.Icon size={16} aria-hidden="true" />
        <span>Theme</span>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          sideOffset={6}
          className="z-50 min-w-36 rounded-xl border border-line bg-surface p-1 text-ink shadow-lg"
        >
          <DropdownMenu.RadioGroup
            value={theme}
            onValueChange={(v) => {
              const t = v as Theme;
              setTheme(t);
              applyTheme(t);
            }}
          >
            {options.map(({ value, label, Icon }) => (
              <DropdownMenu.RadioItem
                key={value}
                value={value}
                className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-ui outline-none data-[highlighted]:bg-paper data-[state=checked]:font-semibold"
              >
                <Icon size={16} aria-hidden="true" />
                {label}
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
