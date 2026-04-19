{ lib, python3, fetchFromGitHub }:

python3.pkgs.buildPythonApplication rec {
  pname = "evim-editor";
  version = "1.0.0";

  src = fetchFromGitHub {
    owner = "tortr-rs";
    repo = "Editor-VIM";
    rev = "v${version}";
    hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  };

  format = "pyproject";
  nativeBuildInputs = [ python3.pkgs.setuptools ];

  meta = with lib; {
    description = "Modal CLI text editor with Vim keybindings";
    homepage = "https://github.com/tortr-rs/Editor-VIM";
    license = licenses.mit;
    maintainers = [ ];
    mainProgram = "evim";
  };
}
