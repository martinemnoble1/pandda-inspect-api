// electron-builder `afterSign` hook: notarize the macOS app — but ONLY when
// Apple credentials are present in the environment. This is what lets the same
// build pipeline produce an UNSIGNED app on forks/PRs (no secrets → no-op) and
// a fully signed+notarized app on a trusted build (secrets set).
//
// Notarization = Apple scans the *already-signed* app and issues a ticket so
// Gatekeeper launches it silently. It is separate from signing (electron-builder
// does the signing with the Developer ID cert from CSC_LINK); this hook runs
// after that and submits the result to Apple's notary service via notarytool.
//
// Required env (set as CI secrets; see packaging/README.md):
//   APPLE_ID                     your Apple ID email
//   APPLE_APP_SPECIFIC_PASSWORD  an app-specific password (appleid.apple.com)
//   APPLE_TEAM_ID                U72A26RZ2R  (Martin Noble, Developer ID team)
"use strict";

const { execFileSync } = require("node:child_process");

exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context;
  if (electronPlatformName !== "darwin") return;

  const { APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID } = process.env;
  if (!APPLE_ID || !APPLE_APP_SPECIFIC_PASSWORD || !APPLE_TEAM_ID) {
    console.log(
      "[notarize] APPLE_* env not set — skipping notarization " +
        "(unsigned build; first launch needs right-click → Open)."
    );
    return;
  }

  const appName = context.packager.appInfo.productFilename;
  const appPath = `${appOutDir}/${appName}.app`;

  // CRITICAL: this afterSign hook also fires when electron-builder DECIDED NOT
  // to sign — most notably on pull requests, where it skips code signing by
  // design ("Current build is a part of pull request, code signing will be
  // skipped"). Notarizing an unsigned app errors. So verify the app is actually
  // signed first and bail gracefully if not (PR builds, or any silent skip).
  try {
    execFileSync("codesign", ["--verify", "--deep", "--strict", appPath], {
      stdio: "ignore",
    });
  } catch {
    console.log(
      "[notarize] app is not validly signed (PR build or signing skipped) " +
        "— skipping notarization. The unsigned app is still produced."
    );
    return;
  }

  // Lazy-require so the dep is only needed for signed mac builds.
  const { notarize } = require("@electron/notarize");
  console.log(`[notarize] submitting ${appName}.app to Apple notary service…`);
  await notarize({
    appPath,
    appleId: APPLE_ID,
    appleIdPassword: APPLE_APP_SPECIFIC_PASSWORD,
    teamId: APPLE_TEAM_ID,
  });
  console.log("[notarize] done — ticket stapled.");
};
