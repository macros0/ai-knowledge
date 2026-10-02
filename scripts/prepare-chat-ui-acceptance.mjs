import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dest = resolve(root, process.argv[2] || "backend/.tmp-search-perf/history-ui-acceptance");
if (!dest.startsWith(root + "/") && !dest.startsWith(root + "\\")) throw new Error("Acceptance directory must be inside the repository");
if (existsSync(dest)) throw new Error("Choose a fresh directory; existing artifacts are never overwritten");
cpSync(join(root, "frontend"), dest, { recursive: true, filter: (path) => !["node_modules", ".next"].includes(path.split(/[\\/]/).at(-1)) });
const config = join(dest, "next.config.js");
writeFileSync(config, readFileSync(config, "utf8").replace('output: "standalone",', 'output: "standalone",\n  reactProductionProfiling: true,'));
const route = join(dest, "src/app/chat-ui-acceptance");
mkdirSync(route, { recursive: true });
cpSync(join(root, "frontend/browser-tests/chat-sources.fixture.jsx"), join(route, "page.jsx"));
const panelRoute = join(dest, "src/app/chat-panel-acceptance");
mkdirSync(panelRoute, { recursive: true });
cpSync(join(root, "frontend/browser-tests/chat-panel.fixture.jsx"), join(panelRoute, "page.jsx"));
console.log(dest);
console.log("Link existing frontend/node_modules into this directory, build with next build --webpack, then start on a free loopback port.");
