"use strict";
const modern = require("minimatch-modern");
const { sep } = require("node:path");

const pathInput = (value) => sep === "\\" ? value.replace(/\\/g, "/") : value;

function adapter(defaults = {}) {
  const options = (value) => ({ ...defaults, ...value, optimizationLevel: 0, platform: "linux" });
  class Matcher extends modern.Minimatch {
    constructor(pattern, value) {
      const opts = options(value);
      if (typeof pattern === "string") {
        pattern = pattern.trim();
        if (sep === "\\" && !opts.allowWindowsEscape) pattern = pattern.replace(/\\/g, "/");
      }
      super(pattern, opts);
    }
    match(value, partial) { return super.match(pathInput(value), partial); }
    // The only makeRe consumer in this toolchain calls .test(). Upstream v11
    // deliberately removed regex generation; keep matching in its safe engine.
    makeRe() { return Object.freeze({ test: (value) => this.match(value) }); }
  }
  function Minimatch(pattern, value) { return new Matcher(pattern, value); }
  Minimatch.prototype = Matcher.prototype;
  Minimatch.defaults = (value) => adapter({ ...defaults, ...value }).Minimatch;
  Minimatch.GLOBSTAR = modern.GLOBSTAR;
  function minimatch(value, pattern, opts) { return new Matcher(pattern, opts).match(value); }
  Object.assign(minimatch, modern, { Minimatch, sep });
  minimatch.minimatch = minimatch;
  minimatch.defaults = (value) => adapter({ ...defaults, ...value });
  minimatch.makeRe = (pattern, value) => new Matcher(pattern, value).makeRe();
  minimatch.filter = (pattern, value) => (input) => minimatch(input, pattern, value);
  minimatch.match = (list, pattern, value) => {
    const result = list.filter(minimatch.filter(pattern, value));
    return result.length || !options(value).nonull ? result : [pattern];
  };
  minimatch.braceExpand = (pattern, value) => modern.braceExpand(pattern, options(value));
  return minimatch;
}

module.exports = adapter();
