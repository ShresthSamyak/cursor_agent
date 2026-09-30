"use strict";
/**
 * Wire types for the Trail bridge protocol v1 (docs/bridge-protocol.md).
 *
 * The core validates events with Pydantic models that forbid unknown fields
 * (trail/core/bus.py: Event, Target), so only the fields declared here are sent,
 * and string lengths are capped to the model limits below. Line numbers on the
 * wire are 1-based, matching stack traces and linters.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.LIMITS = exports.SOURCE = exports.APP = exports.CLIENT = exports.PROTOCOL_VERSION = void 0;
exports.cap = cap;
exports.PROTOCOL_VERSION = 1;
exports.CLIENT = "vscode";
exports.APP = "vscode";
exports.SOURCE = "extension";
/** Field limits from trail/core/bus.py (exceeding one gets the whole frame refused). */
exports.LIMITS = {
    targetText: 8000,
    targetContext: 2000,
    eventText: 32000,
    /** Bridge refuses frames over 256 KB; keep headroom for JSON escaping. */
    frameBytes: 250_000,
};
/** Cap a string to `max` UTF-16 units (never more code points than Python's len()). */
function cap(text, max) {
    return text.length <= max ? text : text.slice(0, max);
}
//# sourceMappingURL=protocol.js.map