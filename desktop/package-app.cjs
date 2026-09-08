const fs = require("node:fs");
const path = require("node:path");
(async () => {
  fs.writeFileSync(
    path.join(__dirname, "runtime-config.json"),
    JSON.stringify({ repoRoot: path.resolve(__dirname, "..") }),
  );
  const { packager } = await import("@electron/packager");
  await packager({
    dir: __dirname,
    out: path.join(__dirname, "out"),
    name: "Multiverse",
    appBundleId: "dev.multiverse.desktop",
    platform: "darwin",
    arch: process.arch,
    overwrite: true,
    ignore: [/^\/out($|\/)/],
    extendInfo: {
      NSAppleEventsUsageDescription:
        "Multiverse reads Chrome tab titles and URLs when you capture your desktop.",
      NSScreenCaptureUsageDescription:
        "Multiverse captures your desktop only when you request a transfer.",
    },
  });
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
