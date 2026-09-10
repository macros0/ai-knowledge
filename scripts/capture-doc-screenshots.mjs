import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const playwrightModule = process.env.PLAYWRIGHT_MODULE || "playwright";
const { chromium } = require(playwrightModule);

export const CAPTURES = [
  ["screenshot-01.png", "/chat"],
  ["screenshot-02.png", "/", "Upload documents"],
  ["screenshot-03.png", "/chat"],
  ["screenshot-04.png", "/chat"],
  ["screenshot-05.png", "/"],
  ["screenshot-06.png", "/"],
  ["screenshot-07.png", "/", "By development"],
  ["screenshot-08.png", "/"],
  ["screenshot-10.png", "/", "Trash"],
  ["screenshot-11.png", "/"],
  ["screenshot-12.png", "/chat/history"],
  ["screenshot-13.png", "/"],
];

const baseUrl = process.env.SCREENSHOT_BASE_URL || "http://localhost:16300";
const backendUrl = process.env.SCREENSHOT_BACKEND_URL || "http://127.0.0.1:18000";
const outputDir = path.resolve(process.env.SCREENSHOT_OUTPUT_DIR || "screenshots");
const username = process.env.SCREENSHOT_USER || "demo.editor";

async function main() {
  await mkdir(outputDir, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_EXECUTABLE || "C:/Program Files/Google/Chrome/Application/chrome.exe",
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    colorScheme: "dark",
  });
  await context.addInitScript(() => {
    localStorage.setItem("okf.locale", "en");
    document.cookie = "okf.locale=en; path=/";
  });
  const page = await context.newPage();

  // Local simulation-auth only. Production/SSO environments are never modified.
  const auth = await page.request.post(`${backendUrl}/api/auth/simulate`, {
    data: { username },
  });
  if (!auth.ok()) {
    throw new Error(`Simulation login failed: HTTP ${auth.status()}`);
  }
  const setCookie = auth.headers()["set-cookie"] || "";
  if (process.env.SCREENSHOT_DEBUG) console.log(`set-cookie: ${setCookie}`);
  const cookies = [...setCookie.matchAll(/(?:^|\n)(csrf_token|session)=([^;\n]+)/g)];
  if (!cookies.some((match) => match[1] === "session")) {
    throw new Error("Simulation login returned no session cookie");
  }
  await context.addCookies(
    cookies.map((match) => ({ name: match[1], value: match[2], domain: "localhost", path: "/" }))
  );

  for (const [filename, route, action] of CAPTURES) {
    await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle" });
    if (action) {
      const control = page.getByRole("button", { name: action, exact: true });
      if (await control.count()) await control.click();
    }
    if (filename === "screenshot-11.png") {
      const duplicate = page.getByRole("button", { name: "Duplicate", exact: true }).first();
      if (await duplicate.count()) await duplicate.click();
    }
    // Documentation captures should show the product UI, not transient dependency
    // health diagnostics caused by the local LLM provider/rate limit.
    await page.addStyleTag({ content: ".health-banner, .toast-container, nextjs-portal { display: none !important; }" });
    await page.screenshot({
      path: path.join(outputDir, filename),
      fullPage: true,
    });
    console.log(`captured ${filename}`);
  }

  await browser.close();
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(import.meta.filename)) {
  if (!existsSync(outputDir)) await mkdir(outputDir, { recursive: true });
  await main();
}
