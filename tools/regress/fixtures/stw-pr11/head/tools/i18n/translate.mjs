#!/usr/bin/env node
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { TARGET_LOCALES } from './locales.mjs';
import { sha256, loadManifest, saveManifest, unitState } from './manifest.mjs';
import { replaceFrontmatterFields, translatableSource, assembleArticle } from './markdown.mjs';
import { collectUiUnits, collectArticleUnits } from './sources.mjs';
import { buildSystemPrompt, buildUiUserPrompt, buildArticleUserPrompt } from './prompt.mjs';
import { createVeniceClient } from './venice.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const UI_JSON = 'src/i18n/ui.json';
const ARTICLES_DIR = 'src/content/anchors';
const MANIFEST = 'tools/i18n/manifest.json';

export function translatableUi(english) { return `UI\n${english}`; }

export async function runTranslate({ uiJson, articlesDir, manifest, client, glossary, mode, write }) {
  const result = { translated: [], skipped: [], stale: [], errors: [] };
  const units = [
    ...collectUiUnits(uiJson),
    ...(articlesDir ? collectArticleUnits(articlesDir) : []),
  ];
  const uiOut = structuredClone(uiJson);

  for (const unit of units) {
    const source = unit.kind === 'ui'
      ? translatableUi(unit.english)
      : translatableSource({ title: unit.title, summary: unit.summary, body: unit.body });
    const hash = sha256(source);
    const state = unitState(manifest, unit.id, hash, TARGET_LOCALES);

    if (state.status === 'current') { result.skipped.push(unit.id); continue; }
    if (mode === 'check') { result.stale.push(unit.id); continue; }
    if (mode === 'dry-run') { result.stale.push(unit.id); continue; }

    const system = (loc) => buildSystemPrompt(loc, glossary);
    const entry = manifest.units[unit.id] ?? { sourceHash: hash, targets: {} };
    entry.sourceHash = hash;
    entry.targets ??= {};

    try {
      for (const loc of state.missing) {
        if (unit.kind === 'ui') {
          const out = await client.completeJSON(system(loc), buildUiUserPrompt(unit.english));
          uiOut[unit.key] = { ...uiOut[unit.key], [loc]: out.translation };
          entry.targets[loc] = { outputHash: sha256(out.translation) };
        } else {
          const out = await client.completeJSON(system(loc), buildArticleUserPrompt(unit));
          const fm = replaceFrontmatterFields(unit.frontmatter, { title: out.title, summary: out.summary });
          const contents = assembleArticle(fm, out.body);
          write(`${articlesDir}/${loc}/${unit.slug}.md`, contents);
          entry.targets[loc] = { outputHash: sha256(contents) };
        }
      }
      manifest.units[unit.id] = entry;
      result.translated.push(unit.id);
    } catch (e) {
      result.errors.push({ id: unit.id, message: e.message });
    }
  }

  if (mode === 'run' && (result.translated.length || Object.keys(uiOut).length)) {
    write(UI_JSON, JSON.stringify(uiOut, null, 2) + '\n');
  }
  return result;
}

// ── CLI ──────────────────────────────────────────────────────────────────
// Load a local .env (if present) so `npm run translate` picks up VENICE_API_KEY
// without a manual `source`. Uses Node's built-in parser (process.loadEnvFile),
// which correctly handles quoting/comments/CRLF; real environment variables take
// precedence and are never overwritten. No-op when no .env exists (e.g. CI),
// where the script falls back to the real environment.
function loadDotEnv(path) {
  try {
    process.loadEnvFile(path);
  } catch (err) {
    // A missing .env (e.g. in CI) is expected — fall back to the real
    // environment. Re-throw anything else (malformed file, permissions) so a
    // real problem surfaces instead of leaving the key silently unset.
    if (err?.code !== 'ENOENT') throw err;
  }
}

function main() {
  loadDotEnv(join(ROOT, '.env'));
  const args = process.argv.slice(2);
  const mode = args.includes('--check') ? 'check' : args.includes('--dry-run') ? 'dry-run' : 'run';
  const uiJson = JSON.parse(readFileSync(join(ROOT, UI_JSON), 'utf8'));
  const manifest = loadManifest(join(ROOT, MANIFEST));
  const glossary = JSON.parse(readFileSync(join(ROOT, 'tools/i18n/glossary.json'), 'utf8'));
  const model = process.env.VENICE_TRANSLATE_MODEL || 'claude-opus-4-8';

  const write = (relOrAbs, contents) => {
    // The orchestrator passes an absolute path for article files (built from the
    // absolute articlesDir) and a repo-relative path for ui.json. Honor both.
    const abs = isAbsolute(relOrAbs) ? relOrAbs : join(ROOT, relOrAbs);
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, contents);
  };

  const client = mode === 'run'
    ? createVeniceClient({ apiKey: process.env.VENICE_API_KEY, model })
    : { completeJSON: async () => { throw new Error('no client in this mode'); } };

  runTranslate({ uiJson, articlesDir: join(ROOT, ARTICLES_DIR), manifest, client, glossary, mode, write })
    .then((res) => {
      if (mode === 'check') {
        if (res.stale.length) {
          console.error(`✗ ${res.stale.length} stale translation unit(s):\n  ${res.stale.join('\n  ')}`);
          console.error('Run `npm run translate` and commit the result.');
          process.exit(1);
        }
        console.warn('✓ All translations current.');
        return;
      }
      manifest.model = model;
      saveManifest(join(ROOT, MANIFEST), manifest);
      console.warn(`Translated ${res.translated.length}, skipped ${res.skipped.length}, errors ${res.errors.length}.`);
      if (res.errors.length) { for (const e of res.errors) console.error(`  ✗ ${e.id}: ${e.message}`); process.exit(1); }
    })
    .catch((e) => { console.error(e.message); process.exit(1); });
}

if (import.meta.url === `file://${process.argv[1]}`) main();
