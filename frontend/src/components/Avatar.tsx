import { avatarColor, initials } from "../lib/labels";

export function Avatar({
  userId,
  name,
  size = 36,
}: {
  userId: string;
  name: string;
  size?: number;
}) {
  return (
    <span
      aria-hidden="true"
      className="inline-flex shrink-0 items-center justify-center rounded-full font-display font-semibold text-white"
      style={{
        width: size,
        height: size,
        backgroundColor: avatarColor(userId),
        fontSize: size * 0.4,
      }}
    >
      {initials(name)}
    </span>
  );
}
