// 共通処理: textlint の依存を入れ、使う設定を決めて実行する。
// Claude Code・Codex・その他のエージェントのどこから呼ばれても動くように、
// エージェント固有の環境変数には依存しない（あれば使う）。
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const skillRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

export const LINTABLE_EXTENSIONS = [".md", ".markdown", ".mdown", ".mkdn", ".txt", ".text"];

// 信頼していないプロジェクトでは JSON の設定だけを自前で読み、パッケージ名の形をしたキーだけを通す。
// パスを書いたルールやフィルタからプロジェクトのコードが読み込まれるのを防ぐため。
const JSON_CONFIGS = [".textlintrc.json", ".textlintrc"];
const ALL_CONFIGS = [".textlintrc", ".textlintrc.json", ".textlintrc.jsonc", ".textlintrc.yaml", ".textlintrc.yml", ".textlintrc.js", ".textlintrc.cjs"];
const PACKAGE_NAME = /^(@[a-z0-9][\w.-]*\/)?[a-z0-9][\w.-]*$/i;

function sanitizedProjectConfig(file) {
  let config;
  try {
    config = JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return null; // YAML や JSONC は信頼したときだけ textlint に読ませる
  }
  if (!config || typeof config !== "object") return null;
  const out = {};
  for (const section of ["rules", "filters", "plugins"]) {
    const value = config[section];
    if (value === undefined) continue;
    const names = Array.isArray(value) ? value : Object.keys(value ?? {});
    if (names.some((n) => typeof n !== "string" || !PACKAGE_NAME.test(n) || n.includes(".."))) {
      throw new Error(`${file} の ${section} にパッケージ名でないものがあります。使うときは TEXTLINT_TRUST_PROJECT=1 を設定してください`);
    }
    out[section] = value;
  }
  // 共有の /tmp で他の利用者に先回りされないよう、推測できない名前の専用ディレクトリ（0700）に新規作成する。
  const dir = mkdtempSync(join(tmpdir(), "ja-writing-textlint-"));
  const tmp = join(dir, "config.json");
  writeFileSync(tmp, JSON.stringify(out), { mode: 0o600, flag: "wx" });
  return { file: tmp, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

const truthy = (v) => ["1", "true", "yes"].includes(String(v ?? "").toLowerCase());

export const trustProject = () =>
  truthy(process.env.TEXTLINT_TRUST_PROJECT) || truthy(process.env.CLAUDE_PLUGIN_OPTION_TRUST_PROJECT_TOOLCHAIN);

// 依存の置き場所。ロックファイルのハッシュごとに分けるので、更新時は新しいディレクトリへ入る。
export function depsDir() {
  if (process.env.TEXTLINT_SKILL_DEPS_DIR) return process.env.TEXTLINT_SKILL_DEPS_DIR;
  const lock = readFileSync(join(skillRoot, "package-lock.json"));
  const hash = createHash("sha256").update(lock).digest("hex").slice(0, 12);
  const cache = process.env.XDG_CACHE_HOME || join(homedir(), ".cache");
  return join(cache, "ja-writing-textlint", hash);
}

const textlintBin = (dir) => join(dir, "node_modules", "textlint", "bin", "textlint.js");

export function ensureInstalled() {
  const dir = depsDir();
  if (existsSync(textlintBin(dir))) return dir;

  // 途中で失敗しても壊れたディレクトリを残さないよう、一時ディレクトリに入れてから置き換える。
  const tmp = `${dir}.tmp-${process.pid}`;
  rmSync(tmp, { recursive: true, force: true });
  mkdirSync(tmp, { recursive: true });
  for (const f of ["package.json", "package-lock.json"]) copyFileSync(join(skillRoot, f), join(tmp, f));

  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  const res = spawnSync(npm, ["ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"], {
    cwd: tmp,
    stdio: ["ignore", "ignore", "pipe"],
    encoding: "utf8",
    shell: process.platform === "win32",
  });
  if (res.status !== 0) {
    rmSync(tmp, { recursive: true, force: true });
    throw new Error(`textlint の依存を入れられませんでした（npm ci）\n${res.stderr ?? res.error}`);
  }
  rmSync(dir, { recursive: true, force: true });
  renameSync(tmp, dir);
  return dir;
}

// 設定の選び方:
//   1. TEXTLINT_CONFIG（明示指定）
//   2. プロジェクトの .textlintrc*（信頼していなければ JSON だけを、検査した写しで使う）
//   3. 同梱の config/textlintrc.json
// プロジェクトを信頼したとき（TEXTLINT_TRUST_PROJECT=1）だけ、YAML・JS の設定と
// プロジェクトの node_modules/textlint をそのまま使う。
export function resolveRunner(projectDir) {
  const find = (names) => names.map((n) => join(projectDir, n)).find(existsSync);

  if (trustProject() && !process.env.TEXTLINT_CONFIG) {
    const projectBin = textlintBin(projectDir);
    const config = find(ALL_CONFIGS);
    if (config && existsSync(projectBin)) return { bin: projectBin, baseArgs: [], source: `project (${config})` };
  }

  const dir = ensureInstalled();
  let config = process.env.TEXTLINT_CONFIG;
  let source = config;
  let cleanup = () => {};
  if (!config && trustProject()) source = config = find(ALL_CONFIGS);
  if (!config && !trustProject()) {
    const projectConfig = find(JSON_CONFIGS);
    const sanitized = projectConfig && sanitizedProjectConfig(projectConfig);
    if (sanitized) {
      [config, source, cleanup] = [sanitized.file, `${projectConfig}（検査済みの写し）`, sanitized.cleanup];
    }
  }
  if (!config) source = config = join(skillRoot, "config", "textlintrc.json");
  return {
    bin: textlintBin(dir),
    baseArgs: ["--config", config, "--rules-base-directory", join(dir, "node_modules")],
    source,
    cleanup,
  };
}

export function runTextlint(args, { projectDir = process.cwd(), ...options } = {}) {
  const runner = resolveRunner(projectDir);
  try {
    const res = spawnSync(process.execPath, [runner.bin, ...runner.baseArgs, ...args], {
      cwd: projectDir,
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
      ...options,
    });
    return { ...res, runner };
  } finally {
    runner.cleanup?.();
  }
}
