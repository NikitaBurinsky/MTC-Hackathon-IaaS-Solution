{
    description = "DevShell";

    outputs = {flake-parts, ...} @ inputs: flake-parts.lib.mkFlake { inherit inputs; } {
        perSystem = { pkgs, ... }: {
            devShells.default = pkgs.mkShell {
                nativeBuildInputs = with pkgs; [
                    python314
                    uv
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
