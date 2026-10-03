import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { ResourceExplorer } from "./ResourceExplorer";
import { requested, stubFetch } from "./testFetch";

const web = { kind_name: "Deployment", scope: "default", name: "web", attributes: { replicas: 3, image: null } };
const db = { kind_name: "Instance", scope: "prod", name: "db", attributes: { shape: { cpus: 2 } } };

test("lists the declared plane first and reports the server's total", async () => {
  const fetchMock = stubFetch(() => ({ body: { count: 7, items: [web, db] } }));
  render(<ResourceExplorer />);
  await waitFor(() => screen.getByText("Deployment · default/web"));
  expect(requested(fetchMock, "GET")).toEqual(["/api/resources?plane=declared&offset=0"]);
  expect(screen.getByTestId("resource-count").textContent).toBe("7");
});

test("switching plane replaces the rows with the discovered plane's", async () => {
  stubFetch((url) => ({
    body: { count: 1, items: [url.includes("plane=discovered") ? db : web] },
  }));
  render(<ResourceExplorer />);
  await waitFor(() => screen.getByText("Deployment · default/web"));
  await userEvent.click(screen.getByRole("button", { name: "DISCOVERED" }));
  await waitFor(() => screen.getByText("Instance · prod/db"));
  expect(screen.queryByText("Deployment · default/web")).toBeNull();
});

test("attributes stay hidden until enter, then show null and nested values legibly", async () => {
  stubFetch(() => ({ body: { count: 2, items: [web, db] } }));
  render(<ResourceExplorer />);
  await waitFor(() => screen.getByText("Deployment · default/web"));
  expect(screen.queryByText("replicas")).toBeNull();
  await userEvent.keyboard("{Enter}");
  expect(screen.getByText("replicas").nextElementSibling?.textContent).toBe("3");
  expect(screen.getByText("image").nextElementSibling?.textContent).toBe("null");
  await userEvent.keyboard("j{Enter}");
  expect(screen.getByText("shape").nextElementSibling?.textContent).toBe('{"cpus":2}');
});

test("load more asks for the next window of the same plane", async () => {
  const fetchMock = stubFetch((url) => ({
    body: { count: 2, items: url.includes("offset=0") ? [web] : [db] },
  }));
  render(<ResourceExplorer />);
  await waitFor(() => screen.getByText("Deployment · default/web"));
  await userEvent.keyboard("m");
  await waitFor(() => screen.getByText("Instance · prod/db"));
  expect(requested(fetchMock, "GET")[1]).toBe("/api/resources?plane=declared&offset=1");
  expect(screen.getByText("Deployment · default/web")).toBeTruthy();
});

test("the filter narrows the loaded rows by kind, scope or name", async () => {
  stubFetch(() => ({ body: { count: 2, items: [web, db] } }));
  render(<ResourceExplorer />);
  await waitFor(() => screen.getByText("Deployment · default/web"));
  await userEvent.type(screen.getByRole("textbox"), "prod");
  expect(screen.queryByText("Deployment · default/web")).toBeNull();
  expect(screen.getByText("Instance · prod/db")).toBeTruthy();
});
