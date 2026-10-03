import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import middleware, { config } from "./middleware";

const credential = btoa("demo:sample-test-password");
const authorization = `Basic ${credential}`;
const hash = createHash("sha256").update(credential).digest("hex");

afterEach(() => vi.unstubAllEnvs());

describe("demo access gate", () => {
  it("covers the homepage, assets and API", () => {
    expect(config.matcher).toBe("/:path*");
  });

  it.each([undefined, "", "not-a-hash"])("fails closed with invalid configuration %s", (value) => {
    vi.stubEnv("DATUM_DEMO_AUTH_SHA256", value);
    expect(middleware(new Request("https://demo.example/")).status).toBe(503);
  });

  it.each(["", "Bearer token", "Basic %%%", `Basic ${btoa("demo:wrong-password")}`,
    `Basic ${"a".repeat(2049)}`])("rejects missing, malformed or incorrect credentials", (value) => {
    vi.stubEnv("DATUM_DEMO_AUTH_SHA256", hash);
    const response = middleware(new Request("https://demo.example/api/discrepancies", {
      headers: { authorization: value },
    }));
    expect(response.status).toBe(401);
    expect(response.headers.get("www-authenticate")).toContain("Basic");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it.each(["/", "/assets/app.js", "/api/discrepancies"])("allows authenticated reads at %s", (path) => {
    vi.stubEnv("DATUM_DEMO_AUTH_SHA256", hash);
    const response = middleware(new Request(`https://demo.example${path}`, {
      headers: { authorization },
    }));
    expect(response.headers.get("x-middleware-next")).toBe("1");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });

  it.each(["", "https://attacker.example", "https://demo.example.attacker.example"])(
    "blocks authenticated writes with an untrusted origin %s", (origin) => {
      vi.stubEnv("DATUM_DEMO_AUTH_SHA256", hash);
      expect(middleware(new Request("https://demo.example/api/discrepancies/1/resolve", {
        method: "POST", headers: { authorization, origin },
      })).status).toBe(403);
    },
  );

  it("allows an authenticated write from the same origin", () => {
    vi.stubEnv("DATUM_DEMO_AUTH_SHA256", hash);
    const response = middleware(new Request("https://demo.example/api/discrepancies/1/resolve", {
      method: "POST", headers: { authorization, origin: "https://demo.example" },
    }));
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });
});
