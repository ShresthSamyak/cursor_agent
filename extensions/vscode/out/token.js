"use strict";
/**
 * Bridge token resolution.
 *
 * Candidates, tried in order until one is accepted:
 *   1. trail.token, if the user set it explicitly;
 *   2. %LOCALAPPDATA%\Trail\bridge.token, then ~/.trail/bridge.token (written by the bridge);
 *   3. trail.token's default, "trail-dev" (used by `python -m trail bridge --dev`).
 * Files are re-read on every connection cycle, so a restarted bridge's new token is picked up.
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.DEV_TOKEN = void 0;
exports.tokenFiles = tokenFiles;
exports.tokenCandidates = tokenCandidates;
const fs = __importStar(require("fs"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
exports.DEV_TOKEN = "trail-dev";
function tokenFiles() {
    const files = [];
    const local = process.env.LOCALAPPDATA;
    if (local)
        files.push(path.join(local, "Trail", "bridge.token"));
    files.push(path.join(os.homedir(), ".trail", "bridge.token"));
    return files;
}
function readToken(file) {
    try {
        const t = fs.readFileSync(file, "utf8").trim();
        // A token is a short opaque string; ignore anything that is clearly not one.
        return t && t.length <= 512 && !/\s/.test(t) ? t : undefined;
    }
    catch {
        return undefined;
    }
}
function tokenCandidates(setting, explicit) {
    const out = [];
    const add = (t) => {
        if (t && !out.includes(t))
            out.push(t);
    };
    if (explicit)
        add(setting);
    for (const f of tokenFiles())
        add(readToken(f));
    add(setting || exports.DEV_TOKEN);
    add(exports.DEV_TOKEN);
    return out;
}
//# sourceMappingURL=token.js.map