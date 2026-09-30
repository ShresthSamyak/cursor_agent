/**
 * Bridge token resolution.
 *
 * Candidates, tried in order until one is accepted:
 *   1. trail.token, if the user set it explicitly;
 *   2. %LOCALAPPDATA%\Trail\bridge.token, then ~/.trail/bridge.token (written by the bridge);
 *   3. trail.token's default, "trail-dev" (used by `python -m trail bridge --dev`).
 * Files are re-read on every connection cycle, so a restarted bridge's new token is picked up.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";

export const DEV_TOKEN = "trail-dev";

export function tokenFiles(): string[] {
  const files: string[] = [];
  const local = process.env.LOCALAPPDATA;
  if (local) files.push(path.join(local, "Trail", "bridge.token"));
  files.push(path.join(os.homedir(), ".trail", "bridge.token"));
  return files;
}

function readToken(file: string): string | undefined {
  try {
    const t = fs.readFileSync(file, "utf8").trim();
    // A token is a short opaque string; ignore anything that is clearly not one.
    return t && t.length <= 512 && !/\s/.test(t) ? t : undefined;
  } catch {
    return undefined;
  }
}

export function tokenCandidates(setting: string | undefined, explicit: boolean): string[] {
  const out: string[] = [];
  const add = (t: string | undefined) => {
    if (t && !out.includes(t)) out.push(t);
  };
  if (explicit) add(setting);
  for (const f of tokenFiles()) add(readToken(f));
  add(setting || DEV_TOKEN);
  add(DEV_TOKEN);
  return out;
}
