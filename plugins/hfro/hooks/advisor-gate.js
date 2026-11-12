#!/usr/bin/env node
// advisor-gate.js - hooks for the hfro plugin.
// advisor-gate.py is a Python port for machines without Node.js. Keep the
// two files in step: any change to one must be made in the other.
//
// Modes (first argument):
//   session-start  SessionStart: print the Advisor workflow with the user's
//                  fallback settings filled in. Plain stdout becomes context.
//   prompt         UserPromptSubmit: record a fingerprint of the git working
//                  tree, so changes made by any means (shell, MCP, subagents)
//                  can be detected later. Prints nothing.
//   review         PostToolUse on Agent/Task: when the advisor returns, record
//                  the fingerprint of the tree it reviewed. Prints nothing.
//   stop           Stop (default): block the main session from finishing when
//                  files changed since the user's latest prompt and the advisor
//                  has not reviewed the latest version, or its latest verdict
//                  was REVISE and nothing was done about it.
//
// The stop gate lets the session finish when: nothing changed, the user said
// noadvisor / no advisor, the advisor approved the latest version, 4 rounds
// with a verdict were used, the session wrote "Advisor: paused" (it is waiting
// for the user) or "Advisor: not run" after a failed call, or the session ignored the gate twice in a row (so it can
// never loop). An advisor call that returned no verdict (for example because
// the model hit its usage limit) does not count as a round; the block message
// then names the fallback model and effort.
//
// Change detection: in a git repository the working tree (tracked and
// untracked, .gitignore respected) is hashed through a private index file in
// the temp directory; this writes git objects but never touches the real index
// or the files. Outside git, only the main session's Write/Edit/MultiEdit/
// NotebookEdit calls are seen; the Advisor workflow text covers the rest.
//
// Plan reviews use a separate subagent (plan-advisor), so they do not count
// as review rounds and never satisfy this gate.

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const MAX_ROUNDS = 4;
const MAX_IGNORED = 2; // blocks in a row that did not lead to a new review round
const AGENT_NAME = 'advisor';
// The advisor as a project agent ("advisor") or from a plugin ("<plugin>:advisor").
// "plan-advisor" and "vpn-advisor" do not match.
const isAdvisor = (t) => typeof t === 'string' && (t === AGENT_NAME || t.endsWith(`:${AGENT_NAME}`));
const EDIT_TOOLS = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit']);
const AGENT_TOOLS = new Set(['Agent', 'Task']);
const SKIP_RE = /(^|[^a-z])no[\s-]?advisor\b/i;
const VERDICT_RE = /VERDICT:\s*\**\s*(APPROVE|REVISE)\b/i;
const ASYNC_RE = /\bagent launched\b|running in the background/i;
const LIMIT_RE = /hit your (session |weekly |usage )?limit|usage limit|rate[ _-]?limit|\b429\b|\b529\b|overloaded|terminated early due to an API error|API Error/i;
const PAUSED_RE = /Advisor:\s*paused/i;
const NOT_RUN_RE = /Advisor:\s*not run/i;
const BLOCK_MARKER = 'the advisor has not reviewed the latest version';
const REVISE_MARKER = 'the advisor asked for changes';
const MODELS = ['opus', 'sonnet', 'haiku', 'fable', 'off'];
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'];
const GIT_TIMEOUT_MS = 5000;

function allow() {
  process.exit(0);
}

function block(reason) {
  process.stdout.write(JSON.stringify({ decision: 'block', reason }));
  process.exit(0);
}

function setting(name, allowed, fallback) {
  const v = String(process.env[`CLAUDE_PLUGIN_OPTION_${name}`] || '').trim().toLowerCase();
  return allowed.includes(v) ? v : fallback;
}

function fallbackSettings() {
  return {
    model: setting('FALLBACK_MODEL', MODELS, 'opus'),
    effort: setting('FALLBACK_EFFORT', EFFORTS, 'high'),
  };
}

function readInput() {
  try {
    const raw = fs.readFileSync(0, 'utf8');
    const data = JSON.parse(raw || '{}');
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

function sessionKey(input) {
  return String(input.session_id || path.basename(input.transcript_path || 'unknown')).replace(/[^\w.-]/g, '_');
}

function statePath(input) {
  return path.join(os.tmpdir(), `hfro-gate-${sessionKey(input)}.json`);
}

function loadState(input) {
  try {
    const s = JSON.parse(fs.readFileSync(statePath(input), 'utf8'));
    return s && typeof s === 'object' ? s : {};
  } catch {
    return {};
  }
}

function saveState(input, state) {
  fs.writeFileSync(statePath(input), JSON.stringify(state));
}

// Hash of the whole working tree (tracked and untracked, .gitignore respected),
// or null outside git or on any error. Uses a private index per session and
// repository, so it never touches the real index. Commits do not change it.
function fingerprint(input) {
  const cwd = input.cwd || process.cwd();
  const git = (args, env) =>
    execFileSync('git', args, {
      cwd,
      env: env || process.env,
      timeout: GIT_TIMEOUT_MS,
      stdio: ['ignore', 'pipe', 'ignore'],
      maxBuffer: 16 * 1024 * 1024,
    }).toString().trim();
  try {
    const top = git(['rev-parse', '--show-toplevel']);
    const gitDir = git(['rev-parse', '--absolute-git-dir']);
    const tag = crypto.createHash('sha1').update(top).digest('hex').slice(0, 12);
    const index = path.join(os.tmpdir(), `hfro-gate-${sessionKey(input)}-${tag}.index`);
    if (!fs.existsSync(index)) {
      try {
        fs.copyFileSync(path.join(gitDir, 'index'), index); // speeds up the first hash
      } catch {
        // New repository with no index yet: git add builds one.
      }
    }
    const env = { ...process.env, GIT_INDEX_FILE: index };
    git(['-C', top, 'add', '-A', '--ignore-errors', '.'], env);
    return `${top}:${git(['-C', top, 'write-tree'], env)}`;
  } catch {
    return null;
  }
}

function modeSessionStart() {
  const file = path.join(__dirname, '..', 'advisor-workflow.md');
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    process.exit(0);
  }
  const fb = fallbackSettings();
  const keep = fb.model === 'off' ? 'off' : 'on';
  const drop = keep === 'on' ? 'off' : 'on';
  text = text
    .replace(new RegExp(`<!-- fallback:${drop} -->[\\s\\S]*?<!-- /fallback:${drop} -->\\n?`, 'g'), '')
    .replace(new RegExp(`<!-- /?fallback:${keep} -->\\n?`, 'g'), '')
    .replace(/\{\{FALLBACK_MODEL\}\}/g, fb.model)
    .replace(/\{\{FALLBACK_EFFORT\}\}/g, fb.effort);
  process.stdout.write(text);
  process.exit(0);
}

function modePrompt(input) {
  const state = loadState(input);
  state.baseline = fingerprint(input);
  state.reviews = {};
  try {
    saveState(input, state);
  } catch {
    // Without a baseline the stop gate falls back to the edit tools.
  }
  process.exit(0);
}

function modeReview(input) {
  const ti = input.tool_input || {};
  if (!AGENT_TOOLS.has(input.tool_name) || !isAdvisor(ti.subagent_type)) process.exit(0);
  const state = loadState(input);
  state.reviews = state.reviews && typeof state.reviews === 'object' ? state.reviews : {};
  state.reviews[String(input.tool_use_id || 'last')] = fingerprint(input);
  try {
    saveState(input, state);
  } catch {
    // The stop gate then compares edit positions only.
  }
  process.exit(0);
}

function textOf(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((p) => (p && typeof p === 'object' ? (p.type === 'text' ? p.text || '' : textOf(p.content)) : ''))
    .join('\n');
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
// notes, this hook's own feedback) are not prompts: counting them would move
// the scan window past the edits.
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
  if (text.includes(BLOCK_MARKER) || text.includes(REVISE_MARKER)) return false;
  // Fallback for entries that carry no origin fields.
  if (/^\s*(<task-notification>|<local-command-stdout>|<local-command-caveat>|\[Request interrupted)/.test(text)) return false;
  return true;
}

function modeStop(input) {
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

  // Scan the main session's tool calls and their results since that prompt.
  let lastEdit = -1;
  let lastText = '';
  const calls = []; // advisor calls: { i, id }
  const results = {}; // tool_use_id -> { text, async }
  for (let i = lastUser + 1; i < entries.length; i++) {
    const e = entries[i];
    if (!e || e.isSidechain || !e.message) continue;
    const content = Array.isArray(e.message.content) ? e.message.content : [];
    if (e.type === 'assistant') {
      for (const p of content) {
        if (!p) continue;
        if (p.type === 'text' && p.text) lastText = p.text;
        if (p.type !== 'tool_use') continue;
        if (EDIT_TOOLS.has(p.name)) lastEdit = i;
        if (AGENT_TOOLS.has(p.name) && p.input && isAdvisor(p.input.subagent_type)) calls.push({ i, id: p.id });
      }
    } else if (e.type === 'user') {
      const tur = e.toolUseResult && typeof e.toolUseResult === 'object' ? e.toolUseResult : {};
      for (const p of content) {
        if (!p || p.type !== 'tool_result' || !p.tool_use_id) continue;
        const text = textOf(p.content);
        results[p.tool_use_id] = {
          text,
          async: tur.isAsync === true || tur.status === 'async_launched' || ASYNC_RE.test(text),
        };
      }
    }
  }

  // A call is a review round when it returned a verdict, was launched in the
  // background (its verdict arrives later), or its result is not in the
  // transcript yet. A call that returned without a verdict failed.
  const verdictOf = (c) => {
    const r = results[c.id];
    const m = r && VERDICT_RE.exec(r.text);
    return m ? m[1].toUpperCase() : null;
  };
  const isReview = (c) => {
    const r = results[c.id];
    return !r || r.async || verdictOf(c) !== null;
  };
  const reviews = calls.filter(isReview);
  const rounds = reviews.length;
  const lastReview = reviews.length ? reviews[reviews.length - 1] : null;
  const lastCall = calls.length ? calls[calls.length - 1] : null;
  const failed = lastCall && !isReview(lastCall) ? results[lastCall.id].text : null;

  const state = loadState(input);
  const now = fingerprint(input);
  const treeChanged = now !== null && typeof state.baseline === 'string' && state.baseline !== now;

  if (lastEdit < 0 && !treeChanged) allow(); // Nothing changed: treat as trivial.
  if (rounds >= MAX_ROUNDS) allow(); // Round limit reached.
  const lastMsg = typeof input.last_assistant_message === 'string' ? input.last_assistant_message : lastText;
  if (PAUSED_RE.test(lastMsg)) allow(); // Waiting for the user's answer.
  if (failed && NOT_RUN_RE.test(lastMsg)) allow(); // No model could review; the user was told.

  let reason = null;
  const reviewedTree = lastReview && state.reviews ? state.reviews[String(lastReview.id)] : undefined;
  const upToDate =
    lastReview &&
    lastReview.i > lastEdit &&
    (now === null || typeof reviewedTree !== 'string' || reviewedTree === now);

  const fb = fallbackSettings();
  const fallbackHint =
    fb.model === 'off'
      ? ''
      : ` If it failed because Fable is at its usage or rate limit or unavailable, call it with model "${fb.model}" and effort "${fb.effort}" (Agent tool parameters), and keep that for the rest of this task.`;

  if (!upToDate) {
    reason = `Files were changed but ${BLOCK_MARKER}. `;
    if (failed) {
      const snippet = failed.replace(/\s+/g, ' ').trim().slice(0, 160);
      reason += `The last advisor call returned no verdict ("${snippet}").` + (LIMIT_RE.test(failed) ? fallbackHint : ' Call it again.') + ' ';
    }
    if (failed && LIMIT_RE.test(failed)) reason += 'If no model can review it, tell the user and end with "Advisor: not run - model limit". ';
    reason += `Call the advisor subagent now in the foreground (round ${rounds + 1} of ${MAX_ROUNDS}) following the result review in the Advisor workflow, then continue the review loop.`;
  } else if (verdictOf(lastReview) === 'REVISE') {
    reason =
      `Round ${rounds} of ${MAX_ROUNDS}: ${REVISE_MARKER} (REVISE) and nothing has been done about it. ` +
      `Fix each issue, or give your reason for disagreeing, then call the advisor again (round ${rounds + 1}). ` +
      `If you need the user's answer before you can go on, end your message with "Advisor: paused - waiting for your answer".`;
  }
  if (!reason) allow();

  // Loop guard: if the gate already blocked for this prompt and the session
  // still has not produced a new review round, that block was ignored. After
  // MAX_IGNORED ignored blocks, let it stop.
  const promptKey = String(entries[lastUser].uuid || lastUser);
  const guard = state.guard && state.guard.prompt === promptKey ? state.guard : { prompt: promptKey, ignored: 0, rounds: -1 };
  guard.ignored = guard.rounds === rounds ? guard.ignored + 1 : 0;
  guard.rounds = rounds;
  const giveUp = guard.ignored >= MAX_IGNORED;
  state.guard = giveUp ? { prompt: promptKey, ignored: 0, rounds: -1 } : guard;
  try {
    saveState(input, state);
  } catch {
    allow(); // Cannot keep the loop guard: do not risk blocking forever.
  }
  if (giveUp) allow();
  block(reason);
}

const mode = process.argv[2] || 'stop';
if (mode === 'session-start') modeSessionStart();
const input = readInput();
try {
  if (mode === 'prompt') modePrompt(input);
  else if (mode === 'review') modeReview(input);
  else modeStop(input);
} catch {
  process.exit(0); // Never block because of a bug in the hook itself.
}
