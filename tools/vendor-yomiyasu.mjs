#!/usr/bin/env node
// yomiyasu（https://github.com/nanaism/yomiyasu, MIT）の skills/yomiyasu を
// plugins/ja-writing/skills/yomiyasu へ写し、各ファイルの先頭に参照元のコメントを入れる。
// 依存として取り込まずに写すのは、上流の変更が自動で入らないようにするため。
//
// 使い方（更新するときも同じ手順）:
//   git clone https://github.com/nanaism/yomiyasu <dir>
//   git -C <dir> checkout <見直したコミット>
//   node tools/vendor-yomiyasu.mjs <dir>
import { spawnSync } from "node:child_process";
import { cpSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = "https://github.com/nanaism/yomiyasu";
// 写さないもの: 上流のプラグイン設定（入れ子のプラグインとして読まれるのを避ける）と、SKILL.md から参照されない画像。
const EXCLUDE = new Set([".claude-plugin", "assets"]);

const src = process.argv[2];
if (!src) {
  console.error("使い方: node tools/vendor-yomiyasu.mjs <yomiyasu を clone したディレクトリ>");
  process.exit(2);
}
const git = (...args) => spawnSync("git", ["-C", src, ...args], { encoding: "utf8" });
const sha = git("rev-parse", "HEAD").stdout.trim();
if (!/^[0-9a-f]{40}$/.test(sha)) throw new Error(`コミットを取得できません: ${src}`);
if (git("status", "--porcelain").stdout.trim()) throw new Error(`作業ツリーに変更があります: ${src}`);

const from = join(src, "skills", "yomiyasu");
const to = join(dirname(fileURLToPath(import.meta.url)), "..", "plugins", "ja-writing", "skills", "yomiyasu");

rmSync(to, { recursive: true, force: true });
cpSync(from, to, { recursive: true, filter: (p) => !EXCLUDE.has(relative(from, p).split(/[\\/]/)[0]) });

function header(rel) {
  const text = `参照元: ${REPO}/blob/${sha}/skills/yomiyasu/${rel} （MIT License。著作権表示は skills/yomiyasu/LICENSE を参照）`;
  switch (extname(rel)) {
    case ".py":
    case ".txt":
      return `# ${text}\n`;
    case ".md":
      return `<!-- ${text} -->\n`;
    default:
      return null;
  }
}

function annotate(file) {
  const rel = relative(to, file).split("\\").join("/");
  // ライセンスの本文は手を加えずに残す。
  if (/LICENSE/.test(rel)) return;
  const h = header(rel);
  if (!h) return;
  const body = readFileSync(file, "utf8");
  let out;
  if (body.startsWith("#!")) {
    // shebang は1行目に残す。
    const nl = body.indexOf("\n") + 1;
    out = body.slice(0, nl) + h + body.slice(nl);
  } else if (rel === "SKILL.md" && body.startsWith("---\n")) {
    // frontmatter は先頭に残す。
    const end = body.indexOf("\n---\n", 4) + 5;
    out = body.slice(0, end) + "\n" + h + body.slice(end);
  } else {
    out = h + body;
  }
  writeFileSync(file, out);
}

(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p);
    else annotate(p);
  }
})(to);

console.log(`${REPO}@${sha} を ${relative(process.cwd(), to)} へ写しました`);
