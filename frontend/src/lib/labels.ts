// Display-only formatting (extension.md §8.5): "western_region" -> "Western region".
// No hard-coded list of groups: any name returned by the backend is formatted the same way.
export function friendlyLabel(name: string): string {
  const words = name.replace(/[_-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function initials(name: string): string {
  const parts = name
    .trim()
    .split(/[\s_]+/)
    .filter(Boolean);
  const letters =
    parts.length > 1 ? [parts[0]![0], parts[parts.length - 1]![0]] : [name[0], name[1]];
  return letters.filter(Boolean).join("").toUpperCase();
}

// Six harmonious hues (§9.5), picked by hashing the user ID.
export const AVATAR_COLORS = ["#0E5A4B", "#7A4E9C", "#B4572E", "#2F6690", "#8A6D1F", "#9C3D54"];

export function avatarColor(userId: string): string {
  let h = 0;
  for (const ch of userId) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length]!;
}

export function formatCount(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}
