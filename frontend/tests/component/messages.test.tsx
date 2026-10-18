import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError } from "../../src/api/client";
import { errorKindOf } from "../../src/features/chat/ChatContext";
import { ERROR_MESSAGES, ErrorMessage } from "../../src/features/chat/ErrorMessage";
import {
  INSUFFICIENT_HINT,
  InsufficientMessage,
} from "../../src/features/chat/InsufficientMessage";

describe("error cards (UI-F12)", () => {
  it.each([
    [429, "You're asking questions quickly. Wait a minute, then try again."],
    [503, "The assistant isn't available right now. Try again in a moment."],
    [422, "That question couldn't be sent. Keep it under 1,000 characters."],
    [500, "Something went wrong while answering. Try again."],
  ])("maps HTTP %i to its message", (status, message) => {
    const kind = errorKindOf(new ApiError(status, null, "rid"))!;
    expect(ERROR_MESSAGES[kind]).toBe(message);
  });

  it("maps network failures and keeps 401 out of the chat", () => {
    expect(ERROR_MESSAGES[errorKindOf(new NetworkError())!]).toBe(
      "Couldn't reach LoanLens. Check your connection and try again.",
    );
    expect(errorKindOf(new ApiError(401, "unauthorized", null))).toBeNull();
  });

  it("shows no codes or ids in visible text; the request id is a hidden attribute", () => {
    const onRetry = vi.fn();
    render(<ErrorMessage kind="unavailable" requestId="req-123" onRetry={onRetry} />);
    const card = screen.getByTestId("error-card");
    expect(card).toHaveAttribute("data-request-id", "req-123");
    expect(card.textContent).not.toMatch(/req-123|503|\{|error/i);
    screen.getByRole("button", { name: "Try again" }).click();
    expect(onRetry).toHaveBeenCalled();
  });
});

describe("insufficient card (UI-F11)", () => {
  it("is neutral, and its hint is the same fixed text for every insufficient answer", () => {
    render(
      <InsufficientMessage answer="I couldn't find information to answer that in the files you have access to." />,
    );
    const card = screen.getByTestId("insufficient");
    expect(card.textContent).toBe(
      "I couldn't find information to answer that in the files you have access to." +
        INSUFFICIENT_HINT,
    );
    expect(INSUFFICIENT_HINT).toContain("check with your admin");
    expect(card.textContent).not.toMatch(/hidden|restricted|not authorized/i);
    expect(card.className).not.toMatch(/coral/);
    expect(card.querySelector('[class*="coral"]')).toBeNull(); // neutral, not the error style
  });
});
