import { Search, X } from "lucide-react";
import { useState } from "react";

import { useGroups, useUserSearch } from "../../api/admin";
import type { AdminUser } from "../../api/types";
import { friendlyLabel } from "../../lib/labels";

type Props = {
  groups: string[];
  users: AdminUser[];
  onGroups: (groups: string[]) => void;
  onUsers: (users: AdminUser[]) => void;
};

/** "Who can read these files": groups from GET /v1/admin/groups, people from /admin/users. */
export function AccessPickers({ groups, users, onGroups, onUsers }: Props) {
  const allGroups = useGroups();
  const [q, setQ] = useState("");
  const found = useUserSearch(q);
  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="text-ui font-semibold text-ink">Groups</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {(allGroups.data ?? []).map((g) => {
            const on = groups.includes(g);
            return (
              <label
                key={g}
                className={`cursor-pointer rounded-full border px-3 py-1 text-ui ${on ? "border-spruce bg-spruce text-on-spruce" : "border-line text-ink hover:bg-paper"}`}
              >
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={on}
                  onChange={() => onGroups(on ? groups.filter((x) => x !== g) : [...groups, g])}
                />
                {friendlyLabel(g)}
              </label>
            );
          })}
        </div>
      </fieldset>
      <div>
        <label htmlFor="people-search" className="text-ui font-semibold text-ink">
          People (optional)
        </label>
        {users.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-2" aria-label="Selected people">
            {users.map((u) => (
              <li
                key={u.user_id}
                className="inline-flex items-center gap-1 rounded-full bg-paper px-3 py-1 text-ui"
              >
                {u.display}
                <button
                  type="button"
                  aria-label={`Remove ${u.display}`}
                  onClick={() => onUsers(users.filter((x) => x.user_id !== u.user_id))}
                  className="rounded-full p-0.5 hover:bg-ink/10"
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="relative mt-2">
          <Search
            size={14}
            aria-hidden="true"
            className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
          />
          <input
            id="people-search"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search people"
            className="w-full rounded-lg border border-line bg-surface py-2 pl-8 pr-3 text-ui"
          />
        </div>
        {q.trim() && (
          <ul
            className="mt-1 max-h-40 overflow-y-auto rounded-lg border border-line"
            aria-label="People found"
          >
            {(found.data ?? [])
              .filter((u) => !users.some((x) => x.user_id === u.user_id))
              .map((u) => (
                <li key={u.user_id}>
                  <button
                    type="button"
                    onClick={() => {
                      onUsers([...users, u]);
                      setQ("");
                    }}
                    className="w-full px-3 py-2 text-left text-ui hover:bg-paper"
                  >
                    {u.display}
                  </button>
                </li>
              ))}
            {found.isSuccess && found.data.length === 0 && (
              <li className="px-3 py-2 text-meta text-ink-muted">No people found.</li>
            )}
          </ul>
        )}
      </div>
    </div>
  );
}
