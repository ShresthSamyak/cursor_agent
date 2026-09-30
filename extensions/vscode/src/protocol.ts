/**
 * Wire types for the Trail bridge protocol v1 (docs/bridge-protocol.md).
 *
 * The core validates events with Pydantic models that forbid unknown fields
 * (trail/core/bus.py: Event, Target), so only the fields declared here are sent,
 * and string lengths are capped to the model limits below. Line numbers on the
 * wire are 1-based, matching stack traces and linters.
 */

export const PROTOCOL_VERSION = 1;
export const CLIENT = "vscode";
export const APP = "vscode";
export const SOURCE = "extension";

/** Field limits from trail/core/bus.py (exceeding one gets the whole frame refused). */
export const LIMITS = {
  targetText: 8000,
  targetContext: 2000,
  eventText: 32000,
  /** Bridge refuses frames over 256 KB; keep headroom for JSON escaping. */
  frameBytes: 250_000,
} as const;

export interface Target {
  text: string;
  context?: string;
  role?: string;
  sensitive?: boolean;
  dwell_ms?: number;
  url?: string;
}

export type EventType =
  | "hover"
  | "dwell"
  | "select"
  | "app_switch"
  | "typing"
  | "save"
  | "test_run"
  | "doc_change"
  | "terminal"
  | "speech_final"
  | "cancel"
  | "not_now";

/** The bus envelope. Only fields the core's Event model accepts. */
export interface BusEvent {
  type: EventType;
  app: string;
  source: string;
  ts: number;
  text?: string;
  barge_in?: boolean;
  target?: Target;
  active?: boolean;
  data?: Record<string, unknown>;
}

export interface ChangedLine {
  line: number;
  text: string;
}

export interface DiagnosticInfo {
  line: number;
  severity: "error" | "warning" | "information" | "hint";
  message: string;
  source?: string;
  code?: string;
}

export interface DocChangeData {
  file: string;
  version: number;
  language: string;
  text: string;
  changed: ChangedLine[];
  diagnostics: DiagnosticInfo[];
  /** Extras (the core accepts any keys inside data). */
  reason: "pause" | "paste" | "open" | "save" | "diagnostics";
  truncated?: boolean;
}

export type ControlFrame =
  | { type: "control"; action: "perception"; on: boolean }
  | { type: "control"; action: "mode"; name: "teach" | "fix" }
  | { type: "control"; action: "agent_mode"; on: boolean }
  | { type: "control"; action: "audit" };

export type ClientFrame =
  | { type: "hello"; client: string; version: number; app: string }
  | { type: "event"; event: BusEvent }
  | ControlFrame;

// ---------------------------------------------------------------- bridge -> client

export interface Output {
  type: string; // speak, speak_start, token, speak_end, duck, unduck, tool_call, tool_cancel, status
  kind?: string | null; // ack, clarify, final, notice
  text?: string;
  version?: number;
  turn?: number;
  code?: string | null;
  meta?: Record<string, unknown>;
}

export interface PendingNotice {
  text: string;
  tier?: string;
}

export interface AgentState {
  agent_mode?: boolean;
  perception?: boolean;
  active_app?: string;
  specialist?: string;
  phase?: string;
  version?: number;
  ducked?: boolean;
  mode?: string;
  pending_notices?: PendingNotice[];
}

export type ServerFrame =
  | { type: "output"; output: Output }
  | { type: "state"; state: AgentState }
  | { type: "audit"; read?: unknown[]; sent_to_cloud?: unknown[] }
  | { type: "error"; code: string; text?: string }
  | { type: string; [key: string]: unknown };

/** Cap a string to `max` UTF-16 units (never more code points than Python's len()). */
export function cap(text: string, max: number): string {
  return text.length <= max ? text : text.slice(0, max);
}
