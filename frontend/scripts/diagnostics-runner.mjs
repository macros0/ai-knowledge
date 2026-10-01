// PID 1 asks the Next worker to flush and release its writer before SIGTERM.
// An unresponsive worker keeps its marker: an operator must inspect it before
// any subsequent writer can claim the spool.
import { fork } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function runServer(serverFile = path.resolve("server.js"), proc = process) {
  const child = fork(serverFile, [], { stdio: ["inherit", "inherit", "inherit", "ipc"], env: proc.env });
  let stopping = false;
  const shutdown = (signal) => {
    if (stopping) return;
    stopping = true;
    let completed = false;
    const forward = () => {
      if (completed) return;
      completed = true;
      clearTimeout(timeout);
      child.kill(signal);
    };
    const timeout = setTimeout(forward, 6000);
    child.on("message", (message) => {
      if (["okf-diagnostics-stopped", "okf-diagnostics-stop-failed"].includes(message?.type)) forward();
    });
    try { child.send({ type: "okf-diagnostics-stop" }, (error) => { if (error) forward(); }); }
    catch { forward(); }
  };
  proc.on("SIGTERM", () => shutdown("SIGTERM"));
  proc.on("SIGINT", () => shutdown("SIGINT"));
  child.on("exit", (code, signal) => { proc.exit(code ?? (signal ? 128 + (signal === "SIGTERM" ? 15 : 2) : 1)); });
  return child;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) runServer();
