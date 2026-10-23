// Not-Happy-Jan for opencode: voice, Vestaboard/displays and hold music via the `nhj` CLI.
// Install:  cp integrations/opencode/nhj.js ~/.config/opencode/plugins/nhj.js
// Needs `nhj` on PATH (or set NHJ_BIN). Events: https://opencode.ai/docs/plugins/
//
//   session busy   -> nhj busy         (hold music; joins the multi-agent policy)
//   session idle   -> nhj idle         (+ nhj vibe ok if the turn ran >= NHJ_OPENCODE_MIN_SECONDS, default 60)
//   permission/question asked -> nhj vibe attn
//   session error  -> nhj vibe err
// Subagent (child) sessions are ignored. NHJ_OPENCODE_DEBUG=1 logs every event type to stderr.
import { spawn } from "node:child_process";

const NHJ = process.env.NHJ_BIN || "nhj";
const MIN_MS = Number(process.env.NHJ_OPENCODE_MIN_SECONDS ?? 60) * 1000;
const DEBUG = !!process.env.NHJ_OPENCODE_DEBUG;
const children = new Set();      // child-session ids (they carry a parentID)
const startedAt = new Map();     // session id -> ms when the current turn went busy

// Commands run ONE AT A TIME, in event order. They are separate processes, so firing them
// independently lets `nhj vibe ok` overtake `nhj idle` (a lone session's result would then be
// chimed instead of spoken) or `idle` overtake `busy` (a finished session left marked busy).
// The handler never waits on the queue, so opencode is not slowed down.
let chain = Promise.resolve();
function nhj(args, payload) {
  chain = chain.then(() => new Promise((resolve) => {
    let settled = false;
    const finish = () => { if (!settled) { settled = true; resolve(); } };
    try {
      const p = spawn(NHJ, args, { stdio: ["pipe", "ignore", "ignore"] });
      const timer = setTimeout(() => { try { p.kill(); } catch { /* already gone */ } finish(); }, 15000);
      const done = () => { clearTimeout(timer); finish(); };
      p.on("close", done);
      p.on("error", done);
      p.stdin.on("error", () => {});
      p.stdin.end(JSON.stringify(payload));
    } catch {
      finish();                           // a notification problem must never break the agent
    }
  }));
}

export const Nhj = async ({ directory }) => {
  const cwd = directory ?? process.cwd();
  return {
    event: async ({ event }) => {
      const type = event?.type;
      const props = event?.properties ?? {};
      if (DEBUG) console.error(`[nhj] event ${type}`);
      const info = props.info;
      if (info?.id && info.parentID) children.add(info.id);
      const sid = props.sessionID ?? info?.id;
      if (sid && children.has(sid)) return;
      const payload = { session_id: sid ? `opencode-${sid}` : "opencode", cwd };

      switch (type) {
        case "session.status": {
          const s = String(props.status?.type ?? props.status).toLowerCase();
          if (["busy", "working", "running"].includes(s) && !startedAt.has(sid)) {
            startedAt.set(sid, Date.now());
            nhj(["busy"], payload);
          }
          break;
        }
        case "permission.asked":
        case "permission.updated":            // older builds
          nhj(["vibe", "attn", "-m", "opencode needs your permission"], payload);
          break;
        case "question.asked":
          nhj(["vibe", "attn", "-m", "opencode has a question"], payload);
          break;
        case "session.error":
          startedAt.delete(sid);
          nhj(["idle"], payload);
          nhj(["vibe", "err", "-m", "opencode hit an error"], payload);
          break;
        case "session.idle": {
          const t0 = startedAt.get(sid);
          if (t0 === undefined) break;        // debounce: idle fires repeatedly
          startedAt.delete(sid);
          nhj(["idle"], payload);
          if (Date.now() - t0 >= MIN_MS) nhj(["vibe", "ok", "-m", "opencode is done"], payload);
          break;
        }
        default:
          break;
      }
    },
  };
};
