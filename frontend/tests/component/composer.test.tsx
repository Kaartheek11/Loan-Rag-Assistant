import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "../../src/features/chat/Composer";

const box = () => screen.getByRole("textbox", { name: "Your question" });

describe("Composer (UI-F6)", () => {
  it("sends on Enter and adds a line on Shift+Enter", async () => {
    const onSend = vi.fn();
    render(<Composer pending={false} onSend={onSend} />);
    await userEvent.type(box(), "first line{Shift>}{Enter}{/Shift}second line");
    expect(box()).toHaveValue("first line\nsecond line");
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.type(box(), "{Enter}");
    expect(onSend).toHaveBeenCalledWith("first line\nsecond line");
    expect(box()).toHaveValue("");
  });

  it("blocks empty and whitespace-only questions", async () => {
    const onSend = vi.fn();
    render(<Composer pending={false} onSend={onSend} />);
    await userEvent.type(box(), "   {Enter}");
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("shows a counter after 800 characters and blocks above 1,000", async () => {
    const onSend = vi.fn();
    render(<Composer pending={false} onSend={onSend} />);
    await userEvent.click(box());
    await userEvent.paste("a".repeat(801));
    expect(screen.getByText("801 / 1,000")).toBeInTheDocument();
    await userEvent.paste("a".repeat(200));
    expect(screen.getByText("Questions can be up to 1,000 characters.")).toBeInTheDocument();
    await userEvent.type(box(), "{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("is disabled while an answer is pending but still accepts typing", async () => {
    const onSend = vi.fn();
    render(<Composer pending onSend={onSend} />);
    await userEvent.type(box(), "next question{Enter}");
    expect(box()).toHaveValue("next question");
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});
