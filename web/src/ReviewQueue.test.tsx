import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { ReviewQueue } from "./ReviewQueue";
import { requested, stubFetch } from "./testFetch";

const disc = {
  id: 1, discrepancy_type: "field", kind_name: "Deployment", scope: "default",
  name: "web", field_name: "replicas",
  declared: { present: true, value: 3 }, discovered: { present: true, value: 5 },
  authoritative_plane: "declared", state: "open",
};
const second = { ...disc, id: 2, name: "api", field_name: "image" };
const third = { ...disc, id: 3, name: "worker", field_name: "memory" };

function serve(items: unknown[], count = items.length) {
  return stubFetch(() => ({ body: { count, items } }));
}

test("shows declared 3 and discovered 5 with declared marked authoritative", async () => {
  serve([disc]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("declared-value").textContent).toBe("3");
  expect(screen.getByTestId("discovered-value").textContent).toBe("5");
  expect(screen.getByTestId("authoritative-badge").textContent).toContain("declared");
});

// The named risk against the review queue is "authoritative side visually
// ambiguous". A badge that names the discovered plane while sitting in the
// declared panel is that ambiguity, so this asserts where the badge is.
test("the authoritative badge sits in the panel of the plane it names", async () => {
  serve([{ ...disc, authoritative_plane: "discovered" }]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  const panel = screen.getByTestId("authoritative-badge").parentElement!;
  expect(panel.contains(screen.getByTestId("discovered-value"))).toBe(true);
  expect(panel.contains(screen.getByTestId("declared-value"))).toBe(false);
});

test("a discrepancy with no authoritative plane shows no badge", async () => {
  serve([{ ...disc, authoritative_plane: null }]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.queryByTestId("authoritative-badge")).toBeNull();
});

// The reason WBS 1.5.0 exists, asserted at the layer where the confusion is
// actually felt. Before it, "intent does not mention this field" and "intent
// requires this field empty" both rendered as the string "null", in front of
// the person deciding what to do about the drift.
test("a field the declared plane never states does not render as null", async () => {
  serve([{ ...disc, declared: { present: false, value: null } }]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("declared-value").textContent).toBe("not stated");
});

test("a field the declared plane states as null renders as null", async () => {
  serve([{ ...disc, declared: { present: true, value: null } }]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("declared-value").textContent).toBe("null");
});

test("a row predating the distinction admits it does not know", async () => {
  serve([{ ...disc, declared: { present: null, value: null } }]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("declared-value").textContent).toBe("—");
});

test("an orphan is explained and shows no field comparison", async () => {
  serve([{ ...disc, discrepancy_type: "discovered_undeclared", field_name: null }]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("UNDECLARED"));
  expect(screen.queryByTestId("declared-value")).toBeNull();
});

test("a type this build does not know is shown under its own name", async () => {
  serve([{ ...disc, discrepancy_type: "quarantined_row" }]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("QUARANTINED ROW"));
});

test("pressing r resolves the focused discrepancy and it leaves the queue", async () => {
  const fetchMock = serve([disc, second]);
  const onResolved = vi.fn();
  render(<ReviewQueue onResolved={onResolved} />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("r");
  await waitFor(() => expect(screen.queryByText("replicas")).toBeNull());
  expect(requested(fetchMock, "POST")).toEqual(["/api/discrepancies/1/resolve"]);
  expect(screen.getByText("image")).toBeTruthy();
  expect(screen.getByTestId("discrepancy-count").textContent).toBe("1");
  expect(onResolved).toHaveBeenCalledTimes(1);
});

test("j moves the focus, so r resolves the second row and not the first", async () => {
  const fetchMock = serve([disc, second]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("jr");
  await waitFor(() => expect(screen.queryByText("image")).toBeNull());
  expect(requested(fetchMock, "POST")).toEqual(["/api/discrepancies/2/resolve"]);
  expect(screen.getByText("replicas")).toBeTruthy();
});

test("r resolves every marked row and leaves the unmarked one", async () => {
  const fetchMock = serve([disc, second, third]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("xjjx");
  expect(screen.getAllByTestId("marked")).toHaveLength(2);
  await userEvent.keyboard("r");
  await waitFor(() => expect(screen.queryByText("memory")).toBeNull());
  expect(requested(fetchMock, "POST")).toEqual([
    "/api/discrepancies/1/resolve", "/api/discrepancies/3/resolve",
  ]);
  expect(screen.getByText("image")).toBeTruthy();
});

test("a refused resolve keeps the row, reports it, and scores nothing", async () => {
  stubFetch((_url, method) =>
    method === "POST" ? { status: 500 } : { body: { count: 1, items: [disc] } });
  const onResolved = vi.fn();
  render(<ReviewQueue onResolved={onResolved} />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("r");
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("500"));
  expect(screen.getByText("replicas")).toBeTruthy();
  expect(onResolved).not.toHaveBeenCalled();
});

test("load more asks for the window after the rows held, and appends it", async () => {
  const fetchMock = stubFetch((url) => ({
    body: { count: 3, items: url.includes("offset=0") ? [disc, second] : [third] },
  }));
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.click(screen.getByRole("button", { name: /load more/i }));
  await waitFor(() => screen.getByText("memory"));
  expect(requested(fetchMock, "GET")).toEqual([
    "/api/discrepancies?state=open&offset=0", "/api/discrepancies?state=open&offset=2",
  ]);
  expect(screen.getByText("replicas")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
});

// Resolving shortens the server's open list, so the next window starts at the
// number of rows still held, not at the number originally fetched.
test("after a resolve, load more does not skip a row", async () => {
  const fetchMock = stubFetch((url, method) => {
    if (method === "POST") return {};
    return { body: { count: 3, items: url.includes("offset=0") ? [disc, second] : [third] } };
  });
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("r");
  await waitFor(() => expect(screen.queryByText("replicas")).toBeNull());
  await userEvent.keyboard("m");
  await waitFor(() => screen.getByText("memory"));
  expect(requested(fetchMock, "GET")[1]).toBe("/api/discrepancies?state=open&offset=1");
});

test("the resolved view asks for resolved rows and r does nothing there", async () => {
  const fetchMock = serve([disc]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.click(screen.getByRole("button", { name: "RESOLVED" }));
  await waitFor(() =>
    expect(requested(fetchMock, "GET")).toContain("/api/discrepancies?state=resolved&offset=0"));
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("r");
  expect(requested(fetchMock, "POST")).toEqual([]);
});

test("the filter narrows the loaded rows, and typing r in it resolves nothing", async () => {
  const fetchMock = serve([disc, second]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("/");
  await userEvent.keyboard("replicas");
  expect(screen.queryByText("image")).toBeNull();
  expect(screen.getByText("replicas")).toBeTruthy();
  expect(requested(fetchMock, "POST")).toEqual([]);
});

test("a failed load says so and retry fetches again", async () => {
  let isDown = true;
  stubFetch(() => (isDown ? { status: 503 } : { body: { count: 1, items: [disc] } }));
  render(<ReviewQueue />);
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("503"));
  isDown = false;
  await userEvent.click(screen.getByRole("button", { name: "RETRY" }));
  await waitFor(() => screen.getByText("replicas"));
});

test("an undecidable field explains the missing policy without claiming authority", async () => {
  serve([{ ...disc, discrepancy_type: "missing_comparison_policy", authoritative_plane: null }]);
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText(/Comparison policy needed/));
  expect(screen.queryByTestId("authoritative-badge")).toBeNull();
  expect(screen.getByTestId("declared-value").textContent).toBe("3");
  expect(screen.getByTestId("discovered-value").textContent).toBe("5");
});
