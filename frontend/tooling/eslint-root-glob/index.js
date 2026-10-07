"use strict";
const { globSync: tinyGlobSync } = require("tinyglobby");
const { expand, EXPANSION_MAX, EXPANSION_MAX_DEPTH, EXPANSION_MAX_LENGTH } = require("brace-expansion");
const { readdirSync, statSync } = require("node:fs");
const { parse, isAbsolute, normalize, resolve } = require("node:path");
const picomatch = require("picomatch");

const isDirectory = (path) => {
  try { return statSync(path).isDirectory(); }
  catch (error) {
    if (error.code === "ENOENT" || error.code === "ENOTDIR") return false;
    throw error;
  }
};

const expandPattern = (pattern) => {
  let depth = 0;
  for (const character of pattern) {
    if (character === "{") depth++;
    if (character === "}") depth = Math.max(0, depth - 1);
    if (depth > EXPANSION_MAX_DEPTH) throw new Error("Glob nesting exceeds supported limit.");
  }
  const expanded = expand(pattern);
  if (expanded.length >= EXPANSION_MAX ||
      expanded.reduce((length, value) => length + value.length, 0) >= EXPANSION_MAX_LENGTH) {
    throw new Error("Glob expansion exceeds supported limit.");
  }
  return expanded.map((value) => value.replace(/(?!^)\/{2,}/g, "/"));
};

const globOne = (pattern) => {
  const root = parse(pattern).root;
  if (root && pattern.replace(/\/$/, "") === root.replace(/\/$/, "")) {
    return isDirectory(pattern) ? [root] : [];
  }
  pattern = pattern.replace(/\/$/, "");
  const scan = picomatch.scan(pattern);
  if (scan.negated) return [];
  if (!scan.isGlob) return isDirectory(pattern) ? [pattern] : [];

  // Normalize only the static base; do not collapse a wildcard followed by "..".
  const base = normalize(scan.base || ".").replace(/\\/g, "/");
  const matchingPattern = base.replace(/\/$/, "") + "/" + scan.glob;
  const matches = picomatch(matchingPattern, { dot: false, posix: true, strictSlashes: true });
  let readError;
  const readDirectory = (directory, options) => {
    try { return readdirSync(directory, options); }
    catch (error) {
      if (error.code !== "ENOENT") readError ??= error;
      throw error;
    }
  };
  // fdir suppresses read errors internally; record them and propagate after the scan.
  const directories = tinyGlobSync(matchingPattern, {
    onlyDirectories: true, expandDirectories: false, fs: { readdirSync: readDirectory },
  });
  if (readError) throw readError;
  // fdir traverses directory symlinks but does not emit the symlink itself.
  const links = tinyGlobSync(matchingPattern, {
    expandDirectories: false, followSymbolicLinks: false,
    fs: { readdirSync: (directory, options) => readDirectory(directory, options).map((entry) =>
      entry.isSymbolicLink() ? {
        name: entry.name,
        isFile: () => true,
        isDirectory: () => false,
        isSymbolicLink: () => false,
      } : entry) },
  }).filter(isDirectory);
  if (readError) throw readError;
  return [...new Set([...directories, ...links].map((path) => path.replace(/\/$/, "")))].filter((path) =>
    matches(isAbsolute(matchingPattern) ? resolve(path).replace(/\\/g, "/") : path));
};

// This is the sole fast-glob API used by @next/eslint-plugin-next 16.3.8.
exports.globSync = (pattern, options) => {
  if (typeof pattern !== "string" || options?.onlyDirectories !== true ||
      Object.keys(options).some((key) => key !== "onlyDirectories")) {
    throw new Error("Next ESLint glob contract changed; review the rootDir adapter.");
  }
  return [...new Set(expandPattern(pattern).flatMap(globOne))];
};
