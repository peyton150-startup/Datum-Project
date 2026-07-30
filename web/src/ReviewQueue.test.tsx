import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { ReviewQueue } from "./ReviewQueue";

const disc = {
  id: 1, discrepancy_type: "field", kind_name: "Deployment", scope: "default",
  name: "web", field_name: "replicas",
  declared: { present: true, value: 3 }, discovered: { present: true, value: 5 },
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

function renderWith(declared: { present: boolean | null; value: unknown }) {
  vi.stubGlobal("fetch", vi.fn(() =>
    Promise.resolve({
      json: () => Promise.resolve({ count: 1, items: [{ ...disc, declared }] }),
    })
  ));
  render(<ReviewQueue />);
}

// The reason WBS 1.5.0 exists, asserted at the layer where the confusion is
// actually felt. Before it, "intent does not mention this field" and "intent
// requires this field empty" both rendered as the string "null", in front of
// the person deciding what to do about the drift.
test("a field the declared plane never states does not render as null", async () => {
  renderWith({ present: false, value: null });
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("declared-value").textContent).toBe("not stated");
});

test("a field the declared plane states as null renders as null", async () => {
  renderWith({ present: true, value: null });
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("declared-value").textContent).toBe("null");
});

test("a row predating the distinction admits it does not know", async () => {
  renderWith({ present: null, value: null });
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("declared-value").textContent).toBe("—");
});

test("pressing r resolves the focused discrepancy and it leaves the queue", async () => {
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("r");
  await waitFor(() => expect(screen.queryByText("replicas")).toBeNull());
});
