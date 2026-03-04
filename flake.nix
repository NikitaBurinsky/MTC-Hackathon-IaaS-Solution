{
    description = "DevShell";

    outputs = {flake-parts, ...} @ inputs: flake-parts.lib.mkFlake { inherit inputs; } {
        perSystem = { system, ... }: let 
                pkgs = import inputs.nixpkgs {
                    inherit system;

                    config = { allowUnfree = true; };
                };
            in {
                _module.args.pkgs = pkgs;

                devShells.default = pkgs.mkShell {
                    nativeBuildInputs = with pkgs; [
                        python314
                        uv

                        codex
                    ];

                    env = {
                        UV_PYTHON = "3.14";
                    };
                };
        };

        systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
    };

    inputs = {
        nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
        flake-parts.url = "github:hercules-ci/flake-parts";
    };
}
