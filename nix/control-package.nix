{ lib, stdenvNoCC, python3, makeWrapper }:
let
  python = python3.withPackages (ps: [ ps.fastapi ps.uvicorn ]);
in
stdenvNoCC.mkDerivation {
  pname = "erebor-dashboard-control";
  version = lib.fileContents ../VERSION;
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../api
      ../actions.json
      ../VERSION
    ];
  };
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ (python3.withPackages (ps: [ ps.fastapi ps.uvicorn ps.httpx ])) ];
  dontBuild = true;
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    python -m unittest discover -s api -v
    runHook postCheck
  '';
  # The package directory (no tests) plus the catalog and version it reads.
  installPhase = ''
    runHook preInstall
    lib="$out/lib/erebor-dashboard"
    install -Dm644 -t "$lib" api/serve.py
    (cd api && find control -name '*.py' -exec install -Dm644 {} "$lib/{}" \;)
    install -Dm644 -t "$lib/control" actions.json VERSION
    makeWrapper ${python}/bin/python3 "$out/bin/erebor-dashboard-control" \
      --add-flags "$lib/serve.py"
    runHook postInstall
  '';
  meta = {
    description = "Loopback FastAPI controls for the dashboard";
    mainProgram = "erebor-dashboard-control";
    platforms = lib.platforms.linux;
  };
}
