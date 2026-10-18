import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { ChatWindow } from "../../src/features/chat/ChatWindow";
import { SourcePanel } from "../../src/features/source/SourcePanel";
import { API, renderApp } from "./utils";

const PAGE_TEXT =
  "BANK STATEMENTS\nThe ending balance on the April\nstatement for account ending 1234 was $4,512.88.\nOther text.";
let queryCalls: string[] = [];
let mode: "answer" | "insufficient" | "busy" = "answer";

const server = setupServer(
  http.get(`${API}/me`, () =>
    HttpResponse.json({
      user_id: "u_alice",
      display_name: "Alice Underwriter",
      groups: ["underwriting"],
      roles: [],
      readable_document_count: 3,
    }),
  ),
  http.post(`${API}/query`, async ({ request }) => {
    const body = (await request.json()) as { question: string };
    queryCalls.push(body.question);
    if (mode === "busy")
      return HttpResponse.json(
        { error: "unavailable" },
        { status: 503, headers: { "X-Request-Id": "r-503" } },
      );
    if (mode === "insufficient")
      return HttpResponse.json({
        request_id: "r2",
        status: "insufficient",
        answer: "I couldn't find information to answer that in the files you have access to.",
        citations: [],
      });
    return HttpResponse.json({
      request_id: "r1",
      status: "answered",
      answer: "The ending balance was $4,512.88 [1]. <img src=x onerror=alert(1)> [7]",
      citations: [
        {
          marker: 1,
          doc_id: "doc-1",
          filename: "loan_000123.pdf",
          page: 63,
          excerpt:
            "The ending balance on the April statement for account ending 1234 was $4,512.88.",
        },
      ],
    });
  }),
  http.get(`${API}/documents/doc-1/pages/:page`, ({ params }) =>
    HttpResponse.json({
      doc_id: "doc-1",
      filename: "loan_000123.pdf",
      page: Number(params.page),
      page_count: 87,
      section: "Bank statements",
      text: Number(params.page) === 63 ? PAGE_TEXT : `Page ${params.page} text.`,
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  queryCalls = [];
  mode = "answer";
});
afterAll(() => server.close());

const ask = async (q: string) => {
  await userEvent.type(screen.getByRole("textbox", { name: "Your question" }), `${q}{Enter}`);
};

describe("chat (UI-F5 to UI-F13)", () => {
  it("shows starters that fill the composer without sending", async () => {
    renderApp(<ChatWindow />);
    expect(await screen.findByText(/, Alice$/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "What's the interest rate on loan ___?" }),
    );
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue(
      "What's the interest rate on loan ___?",
    );
    expect(queryCalls).toEqual([]);
  });

  it("answers with citation chips, plain text, and opens the highlighted source", async () => {
    renderApp(
      <>
        <ChatWindow />
        <SourcePanel breakpoint="desktop" />
      </>,
    );
    await ask("What was the April ending balance on loan LN-2024-000123?");
    const answer = await screen.findByTestId("assistant-answer");
    // [7] has no citation, so it stays text; HTML in answers is shown as text, never parsed.
    expect(answer.textContent).toContain("<img src=x onerror=alert(1)> [7]");
    expect(answer.querySelector("img")).toBeNull();
    const chips = within(answer).getAllByRole("button", {
      name: "Source 1: loan_000123.pdf, page 63",
    });
    expect(chips).toHaveLength(2); // inline chip + Sources row
    await userEvent.click(chips[0]!);
    expect(await screen.findByTestId("cited-passage")).toHaveTextContent(
      "The ending balance on the April statement for account ending 1234 was $4,512.88.",
    );
    expect(screen.getByTestId("page-indicator")).toHaveTextContent("Page 63 of 87");
    expect(screen.getByText("Bank statements")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Page 64 text.")).toBeInTheDocument();
    expect(screen.queryByTestId("cited-passage")).toBeNull(); // highlight only on the cited page
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByTestId("page-body")).toBeNull());
    expect(chips[0]).toHaveFocus(); // focus returns to the chip
  });

  it("shows the neutral card for insufficient answers", async () => {
    mode = "insufficient";
    renderApp(<ChatWindow />);
    await ask("anything");
    expect(await screen.findByTestId("insufficient")).toBeInTheDocument();
  });

  it("shows an error card whose Try again resends the same question", async () => {
    mode = "busy";
    renderApp(<ChatWindow />);
    await ask("What is the rate on loan LN-2024-000001?");
    const card = await screen.findByTestId("error-card");
    expect(card).toHaveAttribute("data-request-id", "r-503");
    mode = "answer";
    await userEvent.click(within(card).getByRole("button", { name: "Try again" }));
    await screen.findByTestId("assistant-answer");
    expect(queryCalls).toEqual([
      "What is the rate on loan LN-2024-000001?",
      "What is the rate on loan LN-2024-000001?",
    ]);
  });
});
