{ lib, stdenvNoCC, python3, makeWrapper }:
let
  python = python3.withPackages (ps: [ ps.fastapi ps.uvicorn ]);
in
stdenvNoCC.mkDerivation {
  pname = "erebor-dashboard-control";
  version = "1.0.0";
  src = ../api;
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ (python3.withPackages (ps: [ ps.fastapi ps.uvicorn ps.httpx ])) ];
  dontBuild = true;
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    python -m unittest discover -v
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    install -Dm644 -t "$out/lib/erebor-dashboard" control.py auth.py
    makeWrapper ${python}/bin/python3 "$out/bin/erebor-dashboard-control" \
      --add-flags "$out/lib/erebor-dashboard/control.py"
    runHook postInstall
  '';
  meta = {
    description = "Loopback FastAPI controls for the Erebor dashboard";
    mainProgram = "erebor-dashboard-control";
    platforms = lib.platforms.linux;
  };
}
