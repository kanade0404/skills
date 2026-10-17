#!/usr/bin/env node
// スキルから使う CLI: `node lint.mjs [textlint のオプション] <ファイルかglob>...`
// textlint のオプション（--fix、--dry-run、-f json など）はそのまま渡す。
import { runTextlint } from "./lib.mjs";

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error("使い方: lint.mjs [textlint のオプション] <ファイルかglob>...");
  process.exit(2);
}

try {
  const res = runTextlint(args, { stdio: ["inherit", "inherit", "inherit"] });
  console.error(`(textlint の設定: ${res.runner.source})`);
  process.exit(res.status ?? 1);
} catch (e) {
  console.error(`ja-writing: ${e.message}`);
  process.exit(2);
}
