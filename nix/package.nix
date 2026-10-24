{ lib, buildNpmPackage, autoPatchelfHook, stdenv, settings ? { } }:

buildNpmPackage {
  pname = "erebor-dashboard";
  version = "2.0.0";
  src = lib.cleanSourceWith {
    src = ../frontend;
    filter = path: _: !(builtins.elem (baseNameOf path) [ "node_modules" "dist" ".angular" ]);
  };
  npmDepsHash = "sha256-ZSDdaAGB7CU8hHVvXC9+zxORO5EABR9S872LPQKjr2w=";
  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib ];
  # Sass embeds a native Dart runtime for the build platform.
  postConfigure = ''
    autoPatchelf node_modules/sass-embedded-linux-${if stdenv.hostPlatform.isAarch64 then "arm64" else "x64"}/dart-sass/src/dart
  '';
  dashboardSettings = builtins.toJSON ({
    title = "Dashboard";
    description = "Services and controls.";
    domainSuffix = "";
    actions = [ ];
    services = [ ];
  } // settings);
  passAsFile = [ "dashboardSettings" ];
  preBuild = ''
    cp "$dashboardSettingsPath" public/config.json
    node --input-type=module <<'JS'
    import { readFileSync, writeFileSync } from 'node:fs';
    const settings = JSON.parse(readFileSync('public/config.json'));
    const path = 'public/manifest.webmanifest';
    const manifest = JSON.parse(readFileSync(path));
    Object.assign(manifest, { name: settings.title + ' services', short_name: settings.title,
      description: settings.description });
    writeFileSync(path, JSON.stringify(manifest));
    const escape = value => value.replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[c]);
    writeFileSync('index.html', readFileSync('index.html', 'utf8')
      .replace('<title>Dashboard</title>', '<title>' + escape(settings.title) + ' services</title>')
      .replace('content="Dashboard"', 'content="' + escape(settings.title) + '"'));
    JS
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -r dist/. "$out/"
    runHook postInstall
  '';
  meta = {
    description = "Configurable Angular service dashboard with offline support";
    platforms = [ "x86_64-linux" "aarch64-linux" ];
  };
}
