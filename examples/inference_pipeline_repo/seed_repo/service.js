import { predict } from "./src/pipeline.js";

const protocol = "shinka-candidate-v1";
let pending = Buffer.alloc(0);

function frame(value) {
  const payload = Buffer.from(JSON.stringify(value), "utf8");
  const header = Buffer.alloc(4);
  header.writeUInt32BE(payload.length, 0);
  return Buffer.concat([header, payload]);
}

function send(value) {
  process.stdout.write(frame(value));
}

function consume() {
  while (pending.length >= 4) {
    const size = pending.readUInt32BE(0);
    if (size === 0 || size > 64 * 1024 * 1024) process.exit(2);
    if (pending.length < size + 4) return;
    const value = JSON.parse(pending.subarray(4, size + 4).toString("utf8"));
    pending = pending.subarray(size + 4);
    if (value.type !== "request" || typeof value.id !== "string") process.exit(2);
    try {
      send({ type: "response", id: value.id, output: predict(value.input) });
    } catch (error) {
      send({ type: "error", id: value.id, error: "candidate request failed" });
    }
  }
}

process.stdin.on("data", (chunk) => {
  pending = Buffer.concat([pending, chunk]);
  consume();
});
send({ type: "ready", protocol });
