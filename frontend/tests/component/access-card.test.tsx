import { act, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { useMe } from "../../src/api/me";
import { AccessCard } from "../../src/features/sidebar/AccessCard";
import { API, renderApp } from "./utils";

let groups = ["underwriting", "western_region"];
const server = setupServer(
  http.get(`${API}/me`, () =>
    HttpResponse.json({
      user_id: "u_alice",
      display_name: "Alice Underwriter",
      groups,
      roles: [],
      readable_document_count: groups.length * 100,
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function Card() {
  const me = useMe();
  return me.data ? <AccessCard me={me.data} /> : null;
}

describe("access card (UI-F4)", () => {
  it("shows friendly labels and updates with a toast when groups change", async () => {
    const { queryClient } = renderApp(<Card />);
    expect(await screen.findByText("Western region")).toBeInTheDocument();
    expect(screen.getByText("200 loan files")).toBeInTheDocument();
    expect(screen.queryByText("Your access was updated.")).not.toBeInTheDocument();

    groups = ["western_region"];
    await act(() => queryClient.refetchQueries({ queryKey: ["me"] }));
    await waitFor(() => expect(screen.queryByText("Underwriting")).not.toBeInTheDocument());
    expect(screen.getByText("100 loan files")).toBeInTheDocument();
    expect(await screen.findByText("Your access was updated.")).toBeInTheDocument();
    expect(screen.getByTestId("access-card")).toHaveAttribute("data-updated", "true");
  });
});
