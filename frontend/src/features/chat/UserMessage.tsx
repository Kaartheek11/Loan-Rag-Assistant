export function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-[18px] rounded-br-[6px] bg-spruce px-4 py-2.5 text-message text-on-spruce">
        {text}
      </p>
    </div>
  );
}
