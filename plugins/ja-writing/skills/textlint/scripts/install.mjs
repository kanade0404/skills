#!/usr/bin/env node
// textlint の依存を先に入れておく。SessionStart フックから呼ぶほか、手で実行してもよい。
// フックの失敗でセッションを止めないよう、常に終了コード 0 で終える。
import { ensureInstalled } from "./lib.mjs";

try {
  const dir = ensureInstalled();
  if (process.stdout.isTTY) console.log(`textlint の依存: ${dir}`);
} catch (e) {
  console.error(`ja-writing: ${e.message}`);
}
process.exit(0);
