{
  description = "Development shell for skills";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { nixpkgs, ... }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-darwin"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pythonVersion = "3.15";
      pythonVersionParts = builtins.match "([0-9]+)\\.([0-9]+)(\\..*)?" pythonVersion;
      pythonPackage = "python${builtins.elemAt pythonVersionParts 0}${builtins.elemAt pythonVersionParts 1}";
    in
    {
      # 開発と CI で使うツールのバージョンは flake.lock で固定する。
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.${pythonPackage};
        in
        {
          default = pkgs.mkShell {
            packages = [
              python
              pkgs.uv
              # textlint の kernel ランナー(TypeScript)の実行と、その依存・開発ツールの管理に使う。
              pkgs.bun
              pkgs.semgrep
              # tools/vendor_yomiyasu.py が上流の写しを取るのに使う。
              pkgs.git
              pkgs.jq
            ];
            # uv はこのシェルの Python を使う(3.9 のテストだけは uv が入れた Python を使う)。
            env.UV_PYTHON = "${python}/bin/python3";
            # semgrep の依存が PYTHONPATH に入り、uv の仮想環境に紛れ込むのを防ぐ(semgrep 本体はラッパーで自分の依存を見つける)。
            shellHook = ''
              unset PYTHONPATH
            '';
          };
        });
    };
}
