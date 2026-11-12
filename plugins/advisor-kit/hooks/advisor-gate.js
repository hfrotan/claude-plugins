#!/usr/bin/env node
// advisor-gate.js - Claude Code Stop hook.
// advisor-gate.py is a Python port for machines without Node.js. Keep the
// two files in step: any change to one must be made in the other.
//
// Blocks the main session from finishing when files were changed since the
// user's latest prompt and the advisor has not reviewed the latest version.
// Lets it finish when: no files changed, the user said noadvisor / no advisor,
// the advisor was called after the last change, 3 rounds were used, or the
// session ignored the gate twice in a row (so it can never loop).
//
// Limits: it only sees the main session's Write/Edit/MultiEdit/NotebookEdit
// calls. Files changed through a shell, an MCP tool or a subagent are not
// detected; the Advisor workflow text covers those. It checks that the advisor was called,
// not what its verdict was.
//
// Plan reviews use a separate subagent (plan-advisor), so they do not count
// as review rounds and never satisfy this gate. The hook does not enforce
// the plan review: whether a task needs one is a judgement the Advisor workflow text covers.

const fs = require('fs');
const os = require('os');
const path = require('path');

const MAX_ROUNDS = 3;
const MAX_IGNORED = 2; // blocks in a row that did not lead to an advisor call
const AGENT_NAME = 'advisor';
// The advisor as a project agent ("advisor") or from a plugin ("<plugin>:advisor").
// "plan-advisor" and "vpn-advisor" do not match.
const isAdvisor = (t) => typeof t === 'string' && (t === AGENT_NAME || t.endsWith(`:${AGENT_NAME}`));
const EDIT_TOOLS = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit']);
const AGENT_TOOLS = new Set(['Agent', 'Task']);
const SKIP_RE = /(^|[^a-z])no[\s-]?advisor\b/i;
const BLOCK_MARKER = 'the advisor has not reviewed the latest version';

function allow() {
  process.exit(0);
}

function block(reason) {
  process.stdout.write(JSON.stringify({ decision: 'block', reason }));
  process.exit(0);
}

let input = {};
try {
  input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
} catch {
  allow();
}

const transcriptPath = input.transcript_path;
if (!transcriptPath || !fs.existsSync(transcriptPath)) allow();

let entries = [];
try {
  entries = fs
    .readFileSync(transcriptPath, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
} catch {
  allow();
}

function promptText(e) {
  const c = e.message.content;
  const raw = typeof c === 'string'
    ? c
    : c.filter((p) => p && p.type === 'text').map((p) => p.text || '').join('\n');
  // Ignore injected system reminders (CLAUDE.md text etc.).
  return raw.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '');
}

// A real prompt typed by the user. Injected entries (system reminders,
// subagent hand-backs, task notifications, compaction summaries, attachment
// notes, this hook's own feedback) are not
// prompts: counting them would move the scan window past the edits.
function isUserPrompt(e) {
  if (!e || e.type !== 'user' || !e.message || e.isSidechain || e.isMeta) return false;
  if (e.isCompactSummary || e.isVisibleInTranscriptOnly) return false;
  if (e.origin && e.origin.kind && e.origin.kind !== 'human') return false;
  if (e.promptSource === 'system') return false;
  const c = e.message.content;
  if (typeof c !== 'string') {
    if (!Array.isArray(c)) return false;
    if (!c.some((p) => p && p.type === 'text')) return false;
    if (c.some((p) => p && p.type === 'tool_result')) return false;
  }
  const text = promptText(e);
  if (!text.trim()) return false;
  if (text.includes(BLOCK_MARKER)) return false;
  // Fallback for entries that carry no origin fields.
  if (/^\s*(<task-notification>|<local-command-stdout>|<local-command-caveat>|\[Request interrupted)/.test(text)) return false;
  return true;
}

// Find the user's latest prompt.
let lastUser = -1;
for (let i = entries.length - 1; i >= 0; i--) {
  if (isUserPrompt(entries[i])) {
    lastUser = i;
    break;
  }
}
if (lastUser < 0) allow();

// User asked to skip the advisor for this task.
if (SKIP_RE.test(promptText(entries[lastUser]))) allow();

// Scan the main session's tool calls since that prompt.
let lastEdit = -1;
let lastAdvisor = -1;
let rounds = 0;

for (let i = lastUser + 1; i < entries.length; i++) {
  const e = entries[i];
  if (!e || e.type !== 'assistant' || e.isSidechain || !e.message) continue;
  const content = Array.isArray(e.message.content) ? e.message.content : [];
  for (const p of content) {
    if (!p || p.type !== 'tool_use') continue;
    if (EDIT_TOOLS.has(p.name)) lastEdit = i;
    if (AGENT_TOOLS.has(p.name) && p.input && isAdvisor(p.input.subagent_type)) {
      lastAdvisor = i;
      rounds++;
    }
  }
}

if (lastEdit < 0) allow();               // No files changed: treat as trivial.
if (rounds >= MAX_ROUNDS) allow();       // Round limit reached.
if (lastAdvisor > lastEdit) allow();     // Latest version already reviewed.

// Loop guard. The hook keeps a small state file per session: if it already
// blocked for this prompt and the session still has not called the advisor,
// that block was ignored. After MAX_IGNORED ignored blocks, let it stop.
const promptKey = String(entries[lastUser].uuid || lastUser);
const sessionKey = String(input.session_id || path.basename(transcriptPath)).replace(/[^\w.-]/g, '_');
const statePath = path.join(os.tmpdir(), `advisor-gate-${sessionKey}.json`);

let state = {};
try {
  state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
} catch {
  state = {};
}
if (state.prompt !== promptKey) state = { prompt: promptKey, ignored: 0, rounds: -1 };

if (state.rounds === rounds) {
  state.ignored++;
} else {
  state.ignored = 0;
}
state.rounds = rounds;

const giveUp = state.ignored >= MAX_IGNORED;
if (giveUp) state = { prompt: promptKey, ignored: 0, rounds: -1 };

try {
  fs.writeFileSync(statePath, JSON.stringify(state));
} catch {
  allow(); // Cannot keep the loop guard: do not risk blocking forever.
}
if (giveUp) allow();

block(
  `Files were changed but ${BLOCK_MARKER}. ` +
  `Call the advisor subagent now (round ${rounds + 1} of ${MAX_ROUNDS}) following the ` +
  `result review in the Advisor workflow, then continue the review loop.`
);
