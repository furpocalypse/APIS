{
  description = "Define development dependencies.";

  inputs = {
    # Which Nix upstream package branch to track
    nixpkgs.url = "nixpkgs/nixos-unstable";
  };

  # What results we're going to expose
  outputs = { self, nixpkgs }:
    let
      # What packages and system functions we'll use
      pkgs = import nixpkgs {  system = "x86_64-linux"; };
      # What version of Python we're going to explicitly track
      # NOTE: This doesn't explicitly track with what is in packages and so
      # will break at some point in the future.
      python_name = "python3.12";
      python = pkgs.python312;
    in {

      # Declare what packages we need as a record. The use as a record is
      # needed because, without it, the data contained within can't be
      # referenced in other parts of this file.
      devShells.x86_64-linux.default = pkgs.mkShell rec {
        packages = [
          pkgs.python3Full
          (pkgs.poetry.override { python3 = python; })
          pkgs.direnv
          pkgs.gcc-unwrapped
          pkgs.stdenv
          # NOTE: Put additional packages you need in this array. Packages may be found by looking them up in
          # https://search.nixos.org/packages
        ];

        # Getting the library paths needed for Python to be put into
        # LD_LIBRARY_PATH
        pythonldlibpath = "${pkgs.stdenv.cc.cc.lib}/lib:${pkgs.stdenv.cc.cc.lib.outPath}/lib:${pkgs.lib.makeLibraryPath packages}";

        # Run the following on the shell, which builds up LD_LIBRARY_PATH.
        shellHook = ''
        export LD_LIBRARY_PATH="${pythonldlibpath}"
        '';

      };
  };
}
