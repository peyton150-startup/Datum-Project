import { Buffer } from "node:buffer";
import { createHash, timingSafeEqual } from "node:crypto";
import { env } from "node:process";
import { next } from "@vercel/functions";

const MAX_AUTHORIZATION_LENGTH = 2048;
const READ_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export default function middleware(request: Request): Response {
  const expectedHash = env.DATUM_DEMO_AUTH_SHA256 ?? "";
  if (!/^[a-f0-9]{64}$/.test(expectedHash)) {
    return new Response("Demo access is not configured.", {
      status: 503,
      headers: { "Cache-Control": "no-store" },
    });
  }

  const authorization = request.headers.get("authorization") ?? "";
  const credential = authorization.length <= MAX_AUTHORIZATION_LENGTH
    ? /^Basic ([A-Za-z0-9+/]+={0,2})$/i.exec(authorization)?.[1]
    : undefined;
  const actualHash = createHash("sha256").update(credential ?? "").digest();
  if (!credential || !timingSafeEqual(actualHash, Buffer.from(expectedHash, "hex"))) {
    return new Response("Demo login required.", {
      status: 401,
      headers: {
        "WWW-Authenticate": 'Basic realm="Datum demo", charset="UTF-8"',
        "Cache-Control": "no-store",
      },
    });
  }

  if (!READ_METHODS.has(request.method) &&
      request.headers.get("origin") !== new URL(request.url).origin) {
    return new Response("Request origin is not allowed.", {
      status: 403,
      headers: { "Cache-Control": "no-store" },
    });
  }

  // Preserve Authorization for Caddy's independent backend access check.
  return next({ headers: { "Cache-Control": "private, no-store" } });
}

export const config = { runtime: "nodejs", matcher: "/:path*" };
