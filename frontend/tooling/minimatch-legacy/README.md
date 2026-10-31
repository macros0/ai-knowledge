# ESLint legacy minimatch adapter

Legacy ESLint plugins call CommonJS minimatch as a function; TypeScript ESLint
uses the modern named API. Legacy callers resolve this adapter, which delegates
matching to upstream minimatch 11.0.3. Modern callers resolve upstream directly.
No vulnerable minimatch implementation is retained.

The tested legacy subset is callable matching, Minimatch/match(partial), defaults,
filter, match, braceExpand and makeRe(...).test(...). v3 whitespace trimming,
Windows slash conversion and case-sensitive drive matching are retained, and
optimizationLevel=0 preserves literal dot-segment semantics. Matching itself
uses the upstream nonrecursive engine.

v11 removed regex generation. `makeRe()` returns a frozen test predicate, not a
RegExp; current eslint-plugin-import/no-internal-modules only invokes `.test`.
Consumers requiring RegExp source/flags or mutating it need a new compatibility
review. Tests exercise the actual import rule plus ordinary paths and partial
directory traversal. This private package is for the pinned lint toolchain only.
