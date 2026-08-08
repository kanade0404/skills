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
      pythonVersion = "3.13";
      pythonVersionParts = builtins.match "([0-9]+)\\.([0-9]+)(\\..*)?" pythonVersion;
      pythonPackage = "python${builtins.elemAt pythonVersionParts 0}${builtins.elemAt pythonVersionParts 1}";
    in
    {
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
              pkgs.nodejs_24
              pkgs.jq
            ];
          };
        });
    };
}
