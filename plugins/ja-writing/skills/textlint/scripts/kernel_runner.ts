// @textlint/kernel で文章を検査・修正する。
//
// 標準入力で JSON の依頼を受け取り、標準出力に JSON の結果を返す。ファイルの読み書きはしない
// (呼び出し側の Python が本文を渡し、修正結果を書き戻す)。
//
// 読み込むモジュールは下の KNOWN_* と PLUGINS に書いたものだけで、設定に書かれた名前からモジュールを探すことはしない。
// プロジェクトの設定は、同梱ルールの有効・無効とオプションの上書きとしてだけ使う。
import type {
  TextlintKernel,
  TextlintKernelFilterRule,
  TextlintKernelOptions,
  TextlintKernelPlugin,
  TextlintKernelRule,
} from "@textlint/kernel";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

type UnknownRecord = Readonly<Record<string, unknown>>;
type Load = (packageName: string) => unknown;

interface Descriptors {
  readonly rules: readonly TextlintKernelRule[];
  readonly filterRules: readonly TextlintKernelFilterRule[];
  readonly plugins: readonly TextlintKernelPlugin[];
  readonly ignored: readonly string[];
}

interface KernelModule {
  readonly TextlintKernel: new () => TextlintKernel;
}

type Entry = readonly [string, unknown];
type RuleModule = TextlintKernelRule["rule"];
type FilterModule = TextlintKernelFilterRule["rule"];
type PluginModule = TextlintKernelPlugin["plugin"];
type RuleOptions = UnknownRecord | true;

interface Preset {
  readonly packageName: string;
  readonly prefix: string;
}

const TECHNICAL_WRITING: Preset = {
  packageName: "textlint-rule-preset-ja-technical-writing",
  prefix: "ja-technical-writing",
};
const SPACING: Preset = { packageName: "textlint-rule-preset-ja-spacing", prefix: "ja-spacing" };
const AI_WRITING: Preset = {
  packageName: "@textlint-ja/textlint-rule-preset-ai-writing",
  prefix: "@textlint-ja/ai-writing",
};

// 設定ファイルに書ける名前(短い名前とパッケージ名)と、読み込むプリセットの対応。
const KNOWN_PRESETS: ReadonlyMap<string, Preset> = new Map([
  ["preset-ja-technical-writing", TECHNICAL_WRITING],
  [TECHNICAL_WRITING.packageName, TECHNICAL_WRITING],
  ["preset-ja-spacing", SPACING],
  [SPACING.packageName, SPACING],
  ["@textlint-ja/preset-ai-writing", AI_WRITING],
  [AI_WRITING.packageName, AI_WRITING],
]);

const KNOWN_FILTERS: ReadonlyMap<string, string> = new Map([
  ["comments", "textlint-filter-rule-comments"],
  ["textlint-filter-rule-comments", "textlint-filter-rule-comments"],
  ["allowlist", "textlint-filter-rule-allowlist"],
  ["textlint-filter-rule-allowlist", "textlint-filter-rule-allowlist"],
]);

const PLUGINS = [
  { packageName: "@textlint/textlint-plugin-markdown", pluginId: "markdown" },
  { packageName: "@textlint/textlint-plugin-text", pluginId: "text" },
] as const;

const isRecord = (value: unknown): value is UnknownRecord =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const recordOrEmpty = (value: unknown): UnknownRecord => {
  if (isRecord(value)) {
    return value;
  }
  return {};
};

// ルールのオプションは、オブジェクトならそのまま、それ以外(true など)なら既定値で有効にする。
const ruleOptions = (value: unknown): RuleOptions => {
  if (isRecord(value)) {
    return value;
  }
  return true;
};

const isRuleModule = (value: unknown): value is RuleModule =>
  typeof value === "function" || (isRecord(value) && typeof value["linter"] === "function");

const isFilterModule = (value: unknown): value is FilterModule => typeof value === "function";

const isPluginModule = (value: unknown): value is PluginModule =>
  isRecord(value) && typeof value["Processor"] === "function";

const isKernelModule = (value: unknown): value is KernelModule =>
  isRecord(value) && typeof value["TextlintKernel"] === "function";

const createLoader = (depsDir: string): Load => {
  // 依存は利用者のキャッシュにあるので、そこを起点に解決する。
  const requireFromDeps = createRequire(path.join(depsDir, "package.json"));
  return (packageName) => {
    const loaded: unknown = requireFromDeps(packageName);
    if (isRecord(loaded) && "default" in loaded) {
      return loaded["default"];
    }
    return loaded;
  };
};

const presetRules = (loaded: unknown, preset: Preset, setting: unknown): TextlintKernelRule[] => {
  if (!isRecord(loaded) || !isRecord(loaded["rules"])) {
    throw new TypeError(`${preset.packageName} はプリセットではありません`);
  }
  const defaults = recordOrEmpty(loaded["rulesConfig"]);
  const overrides = recordOrEmpty(setting);
  return Object.entries(loaded["rules"]).flatMap(([name, rule]: Entry): TextlintKernelRule[] => {
    if (!isRuleModule(rule)) {
      throw new TypeError(`${preset.packageName} の ${name} はルールではありません`);
    }
    let options = defaults[name];
    if (Object.hasOwn(overrides, name)) {
      options = overrides[name];
    }
    if (options === false) {
      return [];
    }
    return [{ options: ruleOptions(options), rule, ruleId: `${preset.prefix}/${name}` }];
  });
};

const loadFilter = (load: Load, packageName: string): FilterModule => {
  const loaded = load(packageName);
  if (!isFilterModule(loaded)) {
    throw new TypeError(`${packageName} はフィルタではありません`);
  }
  return loaded;
};

const loadPlugin = (load: Load, packageName: string): PluginModule => {
  const loaded = load(packageName);
  if (!isPluginModule(loaded)) {
    throw new TypeError(`${packageName} はプラグインではありません`);
  }
  return loaded;
};

const buildDescriptors = (config: UnknownRecord, load: Load): Descriptors => {
  const ignored: string[] = [];
  const rules = Object.entries(recordOrEmpty(config["rules"])).flatMap(
    ([name, setting]: Entry): TextlintKernelRule[] => {
      const preset = KNOWN_PRESETS.get(name);
      if (preset === undefined) {
        ignored.push(name);
        return [];
      }
      if (setting === false) {
        return [];
      }
      return presetRules(load(preset.packageName), preset, setting);
    },
  );
  const filterRules = Object.entries(recordOrEmpty(config["filters"])).flatMap(
    ([name, setting]: Entry): TextlintKernelFilterRule[] => {
      const packageName = KNOWN_FILTERS.get(name);
      if (packageName === undefined) {
        ignored.push(name);
        return [];
      }
      if (setting === false) {
        return [];
      }
      return [{ options: ruleOptions(setting), rule: loadFilter(load, packageName), ruleId: name }];
    },
  );
  const plugins = PLUGINS.map(({ packageName, pluginId }) => ({ plugin: loadPlugin(load, packageName), pluginId }));
  return { filterRules, ignored, plugins, rules };
};

interface Item {
  readonly filePath: string;
  readonly ext: string;
  readonly text: string;
}

interface LintRequest {
  readonly depsDir: string;
  readonly fix: boolean;
  readonly config: UnknownRecord;
  readonly items: readonly Item[];
}

interface ReportedMessage {
  readonly ruleId: string;
  readonly message: string;
  readonly line: number;
  readonly column: number;
  readonly severity: number;
}

interface ItemResult {
  readonly filePath: string;
  readonly messages: readonly ReportedMessage[];
  readonly output?: string;
}

interface LintJob {
  readonly kernel: TextlintKernel;
  readonly item: Item;
  readonly descriptors: Descriptors;
  readonly fix: boolean;
}

const describe = (error: unknown): string => {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
};

const parseItem = (value: unknown): Item => {
  if (isRecord(value)) {
    const { ext, filePath, text } = value;
    if (typeof filePath === "string" && typeof ext === "string" && typeof text === "string") {
      return { ext, filePath, text };
    }
  }
  throw new TypeError("items の要素に filePath・ext・text がありません");
};

const parseRequest = (text: string): LintRequest => {
  const parsed: unknown = JSON.parse(text);
  if (!isRecord(parsed)) {
    throw new TypeError("依頼が JSON のオブジェクトではありません");
  }
  const { config, depsDir, fix, items } = parsed;
  if (typeof depsDir !== "string" || typeof fix !== "boolean" || !isRecord(config) || !Array.isArray(items)) {
    throw new TypeError("依頼に depsDir・fix・config・items がそろっていません");
  }
  return { config, depsDir, fix, items: items.map((item: unknown) => parseItem(item)) };
};

const report = (messages: readonly ReportedMessage[]): ReportedMessage[] =>
  messages.map(({ column, line, message, ruleId, severity }) => ({ column, line, message, ruleId, severity }));

const lintItem = async ({ descriptors, fix, item, kernel }: LintJob): Promise<ItemResult> => {
  const options: TextlintKernelOptions = {
    ext: item.ext,
    filePath: item.filePath,
    filterRules: [...descriptors.filterRules],
    plugins: [...descriptors.plugins],
    rules: [...descriptors.rules],
  };
  if (!fix) {
    const result = await kernel.lintText(item.text, options);
    return { filePath: item.filePath, messages: report(result.messages) };
  }
  const fixed = await kernel.fixText(item.text, options);
  // 直せなかった指摘を知らせるため、直したあとの本文をもう一度検査する。
  const remaining = await kernel.lintText(fixed.output, options);
  return { filePath: item.filePath, messages: report(remaining.messages), output: fixed.output };
};

const readStdin = async (): Promise<string> => {
  process.stdin.setEncoding("utf8");
  let text = "";
  for await (const chunk of process.stdin) {
    const value: unknown = chunk;
    if (typeof value === "string") {
      text += value;
    }
  }
  return text;
};

const run = async (): Promise<void> => {
  const request = parseRequest(await readStdin());
  const load = createLoader(request.depsDir);
  const kernelModule = load("@textlint/kernel");
  if (!isKernelModule(kernelModule)) {
    throw new TypeError("@textlint/kernel を読み込めません");
  }
  const descriptors = buildDescriptors(request.config, load);
  const kernel = new kernelModule.TextlintKernel();
  const results = await Promise.all(
    request.items.map(async (item) => lintItem({ descriptors, fix: request.fix, item, kernel })),
  );
  process.stdout.write(JSON.stringify({ ignored: descriptors.ignored, results }));
};

try {
  await run();
} catch (error: unknown) {
  process.stderr.write(`${describe(error)}\n`);
  process.exitCode = 1;
}
