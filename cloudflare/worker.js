const UPSTREAM = new URL("https://robertzaufall.github.io/tencent-repos/");
const PREFIX = "/tencent/";

export default {
  async fetch(request) {
    const source = new URL(request.url);
    if (source.pathname !== "/tencent" && !source.pathname.startsWith(PREFIX)) {
      return new Response("Not found", { status: 404 });
    }
    if (!["GET", "HEAD"].includes(request.method)) {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    if (source.pathname === "/tencent") {
      source.pathname = PREFIX;
      return Response.redirect(source.href, 308);
    }
    const target = new URL(UPSTREAM);
    target.pathname += source.pathname.slice(PREFIX.length);
    target.search = source.search;
    const forwarded = new Headers();
    for (const name of ["accept", "accept-encoding", "range", "if-range", "if-none-match", "if-modified-since"]) {
      if (request.headers.has(name)) forwarded.set(name, request.headers.get(name));
    }
    let response;
    try {
      response = await fetch(target, { method: request.method, headers: forwarded, redirect: "manual" });
    } catch {
      return new Response("Origin temporarily unavailable", { status: 502 });
    }
    const headers = new Headers(response.headers);
    headers.delete("set-cookie");
    headers.set("x-rob-proxy", "tencent");
    if (headers.get("content-type")?.includes("text/html")) headers.set("cache-control", "no-cache");
    if (headers.has("location")) {
      const location = new URL(headers.get("location"), target);
      if (location.origin === UPSTREAM.origin && location.pathname.startsWith(UPSTREAM.pathname)) {
        location.host = source.host;
        location.protocol = source.protocol;
        location.pathname = PREFIX + location.pathname.slice(UPSTREAM.pathname.length);
        headers.set("location", location.href);
      }
    }
    if (response.status >= 500) return new Response("Origin temporarily unavailable", { status: 502 });
    return new Response(request.method === "HEAD" ? null : response.body, {
      status: response.status, statusText: response.statusText, headers,
    });
  },
};
