import dns from "node:dns/promises";
import http from "node:http";
import net from "node:net";

const allowedSuffixes = (process.env.ALLOWED_HOST_SUFFIXES ?? "")
  .split(",")
  .map((value) => value.trim().toLowerCase().replace(/^\./, ""))
  .filter(Boolean);

if (allowedSuffixes.length === 0) {
  throw new Error("ALLOWED_HOST_SUFFIXES must contain at least one DNS suffix");
}

function isAllowedHost(rawHost) {
  const host = rawHost.toLowerCase().replace(/\.$/, "");
  return (
    net.isIP(host) === 0 &&
    /^[a-z0-9.-]+$/.test(host) &&
    allowedSuffixes.some(
      (suffix) => host === suffix || host.endsWith(`.${suffix}`),
    )
  );
}

function isPublicIPv4(address) {
  const octets = address.split(".").map(Number);
  return !(
    octets[0] === 0 ||
    octets[0] === 10 ||
    (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127) ||
    octets[0] === 127 ||
    (octets[0] === 169 && octets[1] === 254) ||
    (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
    (octets[0] === 192 && octets[1] === 0 && octets[2] === 0) ||
    (octets[0] === 192 && octets[1] === 0 && octets[2] === 2) ||
    (octets[0] === 192 && octets[1] === 88 && octets[2] === 99) ||
    (octets[0] === 192 && octets[1] === 168) ||
    (octets[0] === 198 && (octets[1] === 18 || octets[1] === 19)) ||
    (octets[0] === 198 && octets[1] === 51 && octets[2] === 100) ||
    (octets[0] === 203 && octets[1] === 0 && octets[2] === 113) ||
    octets[0] >= 224
  );
}

function isPublicAddress(address) {
  if (net.isIPv4(address)) return isPublicIPv4(address);
  if (net.isIPv6(address)) {
    const normalized = address.toLowerCase();
    const mapped = normalized.match(/(?:^|:)(\d+\.\d+\.\d+\.\d+)$/)?.[1];
    if (mapped && net.isIPv4(mapped)) return isPublicIPv4(mapped);
    return !(
      normalized === "::" ||
      normalized === "::1" ||
      normalized.startsWith("64:ff9b:") ||
      normalized.startsWith("100::") ||
      normalized.startsWith("100:0:") ||
      normalized.startsWith("2001::") ||
      normalized.startsWith("2001:2:") ||
      /^2001:[12][0-9a-f]:/.test(normalized) ||
      normalized.startsWith("2001:db8:") ||
      normalized.startsWith("2002:") ||
      normalized.startsWith("fc") ||
      normalized.startsWith("fd") ||
      /^fe[89abcdef]/.test(normalized) ||
      normalized.startsWith("ff")
    );
  }
  return false;
}

async function resolvePublic(host) {
  const addresses = await dns.lookup(host, { all: true, verbatim: true });
  const selected = addresses.find(({ address }) => isPublicAddress(address));
  if (!selected) throw new Error("provider hostname has no public address");
  return selected;
}

const server = http.createServer((_request, response) => {
  response.writeHead(405, { Connection: "close" });
  response.end();
});

server.on("clientError", (_error, socket) => {
  // A client can disappear while DNS resolution or the upstream CONNECT is
  // still in flight. Treat that as a closed tunnel instead of allowing an
  // unhandled socket error to terminate the proxy process.
  socket.destroy();
});

server.on("connect", async (request, client, head) => {
  let upstream;
  client.on("error", () => {
    if (upstream && !upstream.destroyed) upstream.destroy();
  });
  client.on("timeout", () => client.destroy());

  try {
    const target = new URL(`http://${request.url}`);
    const host = target.hostname;
    const port = Number(target.port || "443");
    if (port !== 443 || !isAllowedHost(host)) {
      client.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
      return;
    }
    const { address, family } = await resolvePublic(host);
    upstream = net.connect({ host: address, port, family });
    upstream.setTimeout(300_000);
    client.setTimeout(300_000);
    upstream.once("connect", () => {
      client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      if (head.length) upstream.write(head);
      upstream.pipe(client);
      client.pipe(upstream);
    });
    upstream.on("error", () => client.destroy());
    upstream.on("timeout", () => upstream.destroy());
  } catch {
    client.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n");
  }
});

server.listen(8080, "0.0.0.0");
