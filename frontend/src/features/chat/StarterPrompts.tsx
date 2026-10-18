// Fixed templates, never generated from document content (UI-F5).
export const STARTER_PROMPTS = [
  "What's the interest rate on loan ___?",
  "Summarize the conditions of approval for loan ___.",
  "When is the next payment due on loan ___?",
  "Who is the borrower on loan ___?",
];

export function StarterPrompts({ onPick }: { onPick: (template: string) => void }) {
  return (
    <ul className="flex flex-wrap justify-center gap-2" aria-label="Starter questions">
      {STARTER_PROMPTS.map((p) => (
        <li key={p}>
          <button
            type="button"
            onClick={() => onPick(p)}
            className="rounded-full border-2 border-marigold-line bg-transparent px-4 py-1.5 text-ui text-ink transition-colors hover:border-marigold hover:bg-marigold hover:text-on-marigold focus-visible:bg-marigold focus-visible:text-on-marigold"
          >
            {p}
          </button>
        </li>
      ))}
    </ul>
  );
}
