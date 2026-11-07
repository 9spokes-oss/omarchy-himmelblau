// Run JavaScript functions from patched QML fixtures under node:vm.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import vm from 'node:vm';

const root = new URL('../', import.meta.url);

// tests/fixtures/<filename> with assets/<patch> applied.
export function patchedFixture(filename, patch) {
  const work = mkdtempSync(join(tmpdir(), 'omarchy-himmelblau-qml-'));
  try {
    writeFileSync(join(work, filename), readFileSync(new URL(`tests/fixtures/${filename}`, root)));
    execFileSync('patch', ['--batch', '--fuzz=0', '-p1', '-d', work], {
      timeout: 5000,
      input: readFileSync(new URL(`assets/${patch}`, root)),
    });
    return readFileSync(join(work, filename), 'utf8');
  } finally {
    rmSync(work, { recursive: true });
  }
}

// The text of `function name(...) { ... }`, found by brace matching.
export function qmlFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `missing function ${name}`);
  let end = source.indexOf('{', start) + 1;
  let depth = 1;
  while (depth && end < source.length) {
    if (source[end] === '{') depth++;
    if (source[end] === '}') depth--;
    end++;
  }
  assert.equal(depth, 0, `unterminated function ${name}`);
  return source.slice(start, end);
}

// Defines the named functions with `state` as their global object; returns `state`.
export function loadFunctions(source, names, state) {
  const scope = vm.createContext(state);
  for (const name of names) vm.runInContext(qmlFunction(source, name), scope, { timeout: 1000 });
  return state;
}
