# Next ESLint rootDir adapter

The root package uses a scoped npm override for `@next/eslint-plugin-next`:
`fast-glob` resolves to this local development package. `tinyglobby` is pinned
at 0.2.17. Next.js runtime and the original ESLint config/rules stay unchanged.

The supported contract is Next plugin 16.3.8's synchronous `globSync(pattern,
{ onlyDirectories: true })`. Extra options fail explicitly. This is not a
replacement for every fast-glob API. The package is a root dev dependency so
npm resolves its local file reference from the frontend root; clean `npm ci`
requires this directory. The Docker build copies it before installing packages.
The pre-push audit still uses the outgoing commit's package/lock snapshot.

Absolute filesystem roots are checked directly because tinyglobby loses the
root entry on Windows/POSIX. Directory expansion is disabled. A second, non-traversing scan includes
symlink entries omitted by fdir's directory result; `statSync` keeps only real
directories and filters dangling links. The primary scan retains traversal
through directory symlinks. Directory read errors other than ENOENT are propagated explicitly.

`test/eslintRootGlob.test.mjs` checks real plugin rootDir discovery and an
internal-link lint diagnostic on Windows/Linux, including absolute/relative
paths, backslashes, globs, braces, mixed arrays, hidden entries and directory
symlinks/junctions. Array entries are resolved individually by the upstream
plugin; a negative entry does not subtract results from another entry.

When updating Next ESLint, recheck its glob API and all these tests. Remove the
adapter and override together once a compatible upstream release removes the
vulnerable dependency chain. Never use a downgraded Next config, disable rules,
omit dev dependencies or raise the audit threshold to bypass this check.

Static paths are checked without normalization. Glob candidates are filtered
against the original pattern with pinned picomatch 4.0.7 and strict slash
matching, so recursive globs exclude their base and a wildcard followed by
parent traversal is not silently collapsed. Negative patterns select nothing,
even when a directory has a literal leading exclamation mark.

Pinned brace-expansion 5.0.12 preserves numeric, padded and stepped ranges
before matching. Expansion and nesting limits fail explicitly; a partial
expansion is never used for lint discovery. Duplicate separators are removed
and only the static glob base is normalized.
