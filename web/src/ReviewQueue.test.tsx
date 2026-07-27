import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { ReviewQueue } from "./ReviewQueue";

const disc = {
  id: 1, discrepancy_type: "field", kind_name: "Deployment", scope: "default",
  name: "web", field_name: "replicas", declared_value: 3, discovered_value: 5,
  authoritative_plane: "declared", state: "open",
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn((_url: string, opts?: RequestInit) => {
    if (opts?.method === "POST") return Promise.resolve({ json: () => Promise.resolve({}) });
    return Promise.resolve({ json: () => Promise.resolve({ count: 1, items: [disc] }) });
  }));
});

test("shows declared 3 and discovered 5 with declared marked authoritative", async () => {
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("declared-value").textContent).toBe("3");
  expect(screen.getByTestId("discovered-value").textContent).toBe("5");
  expect(screen.getByTestId("authoritative-badge").textContent).toContain("declared");
});

test("pressing r resolves the focused discrepancy and it leaves the queue", async () => {
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("r");
  await waitFor(() => expect(screen.queryByText("replicas")).toBeNull());
});
