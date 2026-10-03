import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import App from "./App";
import { stubFetch } from "./testFetch";

const disc = {
  id: 1, discrepancy_type: "field", kind_name: "Deployment", scope: "default",
  name: "web", field_name: "replicas",
  declared: { present: true, value: 3 }, discovered: { present: true, value: 5 },
  authoritative_plane: "declared", state: "open",
};
const resource = { kind_name: "Instance", scope: "prod", name: "db", attributes: {} };

function serveBoth() {
  return stubFetch((url) => ({
    body: { count: 1, items: [url.startsWith("/api/resources") ? resource : disc] },
  }));
}

test("2 opens the explorer and 1 returns to the queue", async () => {
  serveBoth();
  render(<App />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("2");
  await waitFor(() => screen.getByText("Instance · prod/db"));
  expect(screen.queryByText("replicas")).toBeNull();
  await userEvent.keyboard("1");
  await waitFor(() => screen.getByText("replicas"));
});

test("the score starts at zero and a resolve is worth a hundred", async () => {
  serveBoth();
  render(<App />);
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("score").textContent).toBe("000000");
  await userEvent.keyboard("r");
  await waitFor(() => expect(screen.getByTestId("score").textContent).toBe("000100"));
});
