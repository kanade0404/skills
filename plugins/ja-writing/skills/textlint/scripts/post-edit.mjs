#!/usr/bin/env node
// 書き込み後フック（Claude Code の PostToolUse、Codex の PostToolUse のどちらでも動く）。
// 書き換えたファイルを textlint にかけ、指摘をエージェントに返す。
//
// モードは TEXTLINT_HOOK_MODE（Claude Code ではプラグインの hook_mode 設定）で選ぶ:
//   block（既定）: 指摘を返して直させる / warn: 文脈として渡すだけ / off: 何もしない
import { existsSync, readFileSync } from "node:fs";
import { extname, isAbsolute, relative, resolve } from "node:path";
import { LINTABLE_EXTENSIONS, runTextlint } from "./lib.mjs";

const mode = process.env.TEXTLINT_HOOK_MODE || process.env.CLAUDE_PLUGIN_OPTION_HOOK_MODE || "block";
if (mode === "off") process.exit(0);

const input = JSON.parse(readFileSync(0, "utf8"));
const projectDir = process.env.CLAUDE_PROJECT_DIR || input.cwd || process.cwd();

// Claude Code の Write/Edit/MultiEdit は tool_input.file_path を持つ。
// Codex の apply_patch は tool_input.command にパッチ本文を持つので、そこからパスを拾う。
function touchedFiles() {
  const ti = input.tool_input ?? {};
  if (typeof ti.file_path === "string") return [ti.file_path];
  const patch = Array.isArray(ti.command) ? ti.command.join("\n") : ti.command;
  if (typeof patch !== "string") return [];
  return [...patch.matchAll(/^\*\*\* (?:Add File|Update File|Move to): (.+)$/gm)].map((m) => m[1].trim());
}

// 同梱ルールは日本語向けなので、かなを含まないファイルは誤検知しか出さない。
const hasKana = (file) => /[\p{Script=Hiragana}\p{Script=Katakana}]/u.test(readFileSync(file, "utf8"));

const files = [...new Set(touchedFiles().map((f) => (isAbsolute(f) ? f : resolve(projectDir, f))))].filter(
  (f) => existsSync(f) && LINTABLE_EXTENSIONS.includes(extname(f).toLowerCase()) && hasKana(f),
);
if (files.length === 0) process.exit(0);

let res;
try {
  res = runTextlint(["--format", "json", ...files], { projectDir });
} catch (e) {
  console.error(`ja-writing: ${e.message}`);
  process.exit(0);
}

let results;
try {
  results = JSON.parse(res.stdout);
} catch {
  // 設定の誤りやルールの不足。編集は止めずに知らせるだけにする。
  console.error(`ja-writing: textlint を実行できませんでした（${res.runner.source}）\n${res.stderr || res.stdout}`);
  process.exit(0);
}

// severity: 1 = warning, 2 = error, 3 = info。info の提案はフックでは扱わない。
const lines = results.flatMap((r) =>
  r.messages
    .filter((m) => m.severity === 1 || m.severity === 2)
    .map((m) => `${relative(projectDir, r.filePath) || r.filePath}:${m.line}:${m.column} [${m.ruleId}] ${m.message}`),
);
if (lines.length === 0) process.exit(0);

const report =
  `textlint の指摘が ${lines.length} 件あります。\n${lines.join("\n")}\n\n` +
  "指摘された箇所を自然な日本語に書き直してください。指摘を消すために内容を削らないでください。" +
  "誤検知と判断したものはそのまま残し、理由を伝えてください。";

const output =
  mode === "warn"
    ? { hookSpecificOutput: { hookEventName: "PostToolUse", additionalContext: report } }
    : { decision: "block", reason: report };
process.stdout.write(JSON.stringify(output));
