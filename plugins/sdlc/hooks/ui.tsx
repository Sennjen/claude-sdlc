import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Approval, ExtraFiles, SdlcState, Stage, StageInfo } from '../types'

// The gates stay in sdlc.py (command hooks). This module only shows their state and
// sends user-only commands through the same UserPromptSubmit handler a typed
// `sdlc approve ...` reaches, so approvals keep one code path and one audit trail.
//
// Two sites:
// - the band above the prompt: feature, stage, status and the one action that fits now;
// - `SDLC` in the prompt footer: unfolds the band into the details (every artifact with
//   its state and a link that opens it, the session switches, the same actions, `Init`
//   where the repository has no SDLC yet) and folds it back.
// Actions that start a skill run its slash command at once, as if the person typed it.
// Claude can only ask for a test unlock (the `request_test_unlock` tool); the person presses it.

const REFRESH_MS = 10_000
const STAGES: readonly Stage[] = ['intent', 'spec', 'plan']

const stateAtom = atom({ plugin: 'sdlc', key: 'state' } as const, null)
const errorAtom = atom({ plugin: 'sdlc', key: 'error' } as const, null)
const busyAtom = atom({ plugin: 'sdlc', key: 'busy' } as const, false)
const filesAtom = atom({ plugin: 'sdlc', key: 'files' } as const, {
  progress: false,
  approvals: false,
  steps: { done: 0, total: 0 },
})
const detailsAtom = atom({ plugin: 'sdlc', key: 'details' } as const, false)
const hiddenAtom = atom({ plugin: 'sdlc', key: 'hidden' } as const, false)
const unlockAtom = atom({ plugin: 'sdlc', key: 'unlockRequest' } as const, null)

// The model's way to ask for the test lock to be lifted. It unlocks nothing: the band then
// offers `Unlock tests`, and only the person's press (or typed command) unlocks.
const UNLOCK_TOOL = 'request_test_unlock'
const UNLOCK_TOOL_SPEC = {
  name: UNLOCK_TOOL,
  description:
    'Ask the user to unlock the SDLC test lock. Call it when a locked test file has to change: ' +
    'the test itself is wrong, or a review found cases that need new tests. It does not unlock ' +
    'anything; the user presses "Unlock tests" in the SDLC bar or types `sdlc unlock tests`. ' +
    'After calling it, stop and wait for the user.',
  inputSchema: {
    type: 'object',
    properties: { reason: { type: 'string', description: 'Why the tests must change, in one sentence.' } },
    required: ['reason'],
  },
}

type On = Extract<SdlcState, { enabled: true }>
type Steps = ExtraFiles['steps']
type UnlockRequest = { reason: string } | null
type Action = { key: string; label: string; run: () => unknown }

const script = ($: EngineInterface) => `${$.plugin.root}/scripts/sdlc.py`
const short = (sha: string | null | undefined) => (sha ? sha.slice(0, 12) : '-')
const clock = (at: string | null | undefined) => (at ? at.replace('T', ' ').slice(0, 16) : '')
const featureDir = (s: On) => (s.stages.intent?.path ?? '').replace(/\/intent\.md$/, '')
const fileUrl = (root: string, rel: string) => `file://${encodeURI(`${root}/${rel}`)}`

// ------------------------------------------------------------------- reading state

let inflight: Promise<SdlcState | null> | null = null

async function fetchState($: EngineInterface): Promise<SdlcState | null> {
  const sid = await $.session.id()
  const cwd = await $.session.cwd()
  try {
    const r = await $.process.run(['python3', script($), 'cli', 'state', '--session', sid], {
      cwd,
      env: { CLAUDE_PROJECT_DIR: cwd },
      timeoutMs: 15_000,
    })
    if (r.exitCode !== 0) {
      await update($, errorAtom, () => `sdlc.py state exited ${r.exitCode}: ${r.stderr.trim().slice(0, 200)}`)
      return null
    }
    await update($, errorAtom, () => null)
    return JSON.parse(r.stdout) as SdlcState
  } catch (err) {
    await update($, errorAtom, () => `cannot run sdlc.py: ${String(err).slice(0, 200)}`)
    return null
  }
}

const NO_STEPS = { done: 0, total: 0 }
// plan.md lists its steps as `1. [ ] ...` under `## Work sequence` and is never edited
// after approval; progress.md records a finished step as `- [x] Step 3: ...`.
const PLAN_STEP = /^\s*(\d+)\.\s+\[[ xX]\]/gm
const DONE_STEP = /^\s*-\s*\[[xX]\]\s*Step\s+(\d+)\b/gim

/** Steps of the plan's work sequence, and how many of them progress.md marks done. */
function countSteps(plan: string, progress: string): { done: number; total: number } {
  const work = plan.split(/^##\s+/m).find(section => section.startsWith('Work sequence')) ?? ''
  const numbers = [...work.matchAll(PLAN_STEP)].map(m => Number(m[1]))
  const done = new Set([...progress.matchAll(DONE_STEP)].map(m => Number(m[1])).filter(n => numbers.includes(n)))
  return { done: done.size, total: numbers.length }
}

async function statFiles($: EngineInterface, s: SdlcState): Promise<ExtraFiles> {
  if (!s.enabled || !s.active) return { progress: false, approvals: false, steps: NO_STEPS }
  const dir = `${s.root}/${featureDir(s)}`
  const isFile = (name: string) =>
    $.fs.stat(`${dir}/${name}`).then(
      st => st.kind === 'file',
      () => false,
    )
  const text = (name: string) => $.fs.read(`${dir}/${name}`).catch(() => '')
  const steps = s.stage === 'build' ? countSteps(await text('plan.md'), await text('progress.md')) : NO_STEPS
  return { progress: await isFile('progress.md'), approvals: await isFile('approvals.json'), steps }
}

function announce($: EngineInterface, prev: SdlcState | null, next: SdlcState) {
  if (!prev || !prev.enabled || !next.enabled) return
  if (prev.active === next.active) {
    for (const stage of STAGES) {
      if (prev.stages[stage]?.state === 'approved' && next.stages[stage]?.state === 'stale') {
        $.ui.toast(`${stage}.md changed after approval: it needs a new approval`, { timeoutMs: 8000 })
      }
    }
  }
  if (prev.code_gate !== next.code_gate) {
    $.ui.toast(next.code_gate === 'open' ? 'SDLC code gate is open' : 'SDLC code gate is closed')
  }
  const last = next.verify.last
  if (last && last.at !== prev.verify.last?.at) {
    $.ui.toast(last.ok ? 'SDLC verify passed' : 'SDLC verify failed: Definition of Done not met', {
      timeoutMs: last.ok ? 4000 : 8000,
    })
  }
  if (prev.active !== next.active) return
  const review = next.review
  if (review && review.at !== prev.review?.at) {
    $.ui.toast(review.verdict === 'ready' ? 'SDLC review passed: the PR is next'
      : 'SDLC review: changes required', { timeoutMs: 8000 })
  }
  if (next.pr && next.pr.url !== prev.pr?.url) $.ui.toast(`PR created: ${next.pr.url}`, { timeoutMs: 8000 })
}

async function refresh($: EngineInterface): Promise<SdlcState | null> {
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const next = await fetchState($)
      if (!next) return null
      const files = await statFiles($, next)
      const prev = await read($, stateAtom)
      announce($, prev, next)
      // A band hidden with its × comes back when there is something new to do.
      const where = (s: SdlcState | null) =>
        s && s.enabled ? `${s.active}/${s.stage}/${s.stage_state}/${reviewState(s)}/${!!s.pr}` : ''
      if (prev && where(prev) !== where(next)) await update($, hiddenAtom, () => false)
      // A request ends once the tests are unlocked, by the button or a typed command.
      if (!next.enabled || !next.tests_locked) await update($, unlockAtom, () => null)
      await update($, filesAtom, () => files)
      await update($, stateAtom, () => next)
      return next
    } finally {
      inflight = null
    }
  })()
  return inflight
}

// ------------------------------------------------------------------- user commands

/** Runs a user-only `sdlc ...` command through sdlc.py's UserPromptSubmit handler. */
async function runUserCommand($: EngineInterface, line: string): Promise<string | null> {
  const sid = await $.session.id()
  const cwd = await $.session.cwd()
  const payload = { session_id: sid, cwd, hook_event_name: 'UserPromptSubmit', prompt: line }
  try {
    const r = await $.process.run(['python3', script($), 'hook', 'prompt'], {
      cwd,
      stdin: JSON.stringify(payload),
      timeoutMs: 20_000,
    })
    if (r.exitCode !== 0 || !r.stdout.trim()) return null
    const out = JSON.parse(r.stdout) as { hookSpecificOutput?: { additionalContext?: string } }
    return out.hookSpecificOutput?.additionalContext?.split('\n')[0] ?? null
  } catch {
    return null
  }
}

/** Presses a user command, tells the model what happened, and refreshes the view. */
async function press($: EngineInterface, line: string): Promise<string | null> {
  if (await read($, busyAtom)) return null
  await update($, busyAtom, () => true)
  try {
    const msg = await runUserCommand($, line)
    if (!msg) {
      $.ui.toast(`sdlc: \`${line}\` failed; type it in the chat instead`, { timeoutMs: 8000 })
      return null
    }
    // The record is already written; if the note to the model is refused (or the engine
    // predates $.session.append, as 2.1.284 does), the model still reads the new state
    // in the next prompt's [SDLC] status line.
    try {
      // @ts-ignore -- missing from the 2.1.284 types; the validator needs the plain `$.` call
      await $.session.append({
        message: {
          type: 'user',
          content: [{ type: 'text', text: `${msg} (The user pressed \`${line}\` in the SDLC UI.)` }],
        },
      })
    } catch {
      // nothing to undo
    }
    $.ui.toast(msg.replace(/^\[SDLC\]\s*/, '').split('. ')[0] ?? msg)
    await refresh($)
    return msg
  } finally {
    await update($, busyAtom, () => false)
  }
}

/** Folds and unfolds the band's details (SDLC in the footer, the feature's name); a band
 *  hidden with its × comes back unfolded. */
async function toggleDetails($: EngineInterface) {
  void refresh($)
  const wasHidden = await read($, hiddenAtom)
  await update($, hiddenAtom, () => false)
  await update($, detailsAtom, open => wasHidden || !open)
}

/** Puts a command in the prompt for the person to send; `hint` says what is left to type. */
async function fillCommand($: EngineInterface, command: string, hint?: string) {
  await update($, detailsAtom, () => false)
  const filled = await $.prompt.fill({ text: `${command} ` })
  $.ui.toast(filled.isFilled ? hint ?? `Press Enter to run ${command}` : `Type ${command} in the prompt`)
}

/** Runs a slash command as if the person typed it and pressed Enter (queued while a turn
 *  runs); where it cannot run, puts it in the prompt instead. */
async function runCommand($: EngineInterface, command: string, args = '') {
  await update($, detailsAtom, () => false)
  const typed = args ? `${command} ${args}` : command
  $.ui.toast(`Running ${typed}`)
  void $.command.run({ command: command.replace(/^\//, ''), args }).catch(() => fillCommand($, typed))
}

/** Shows one of the desktop's own panes (Files, Diff); false where there is none. */
async function showPane($: EngineInterface, args: { pane: 'file' | 'diff'; path?: string }): Promise<boolean> {
  try {
    if (!(await $.mcp.call('ccd_view', 'show_pane', args)).isError) return true
  } catch {
    // not the desktop, or the server is not reachable this way
  }
  try {
    const shown = (await $.tool.call({ tool: 'mcp__ccd_view__show_pane', ...args } as never)) as {
      deny?: string
      isError?: boolean
    }
    if (!shown.deny && !shown.isError) return true
  } catch {
    // no such tool: not the desktop
  }
  return false
}

/** Opens the session's changes in the desktop's Diff pane. */
async function showChanges($: EngineInterface) {
  if (!(await showPane($, { pane: 'diff' }))) $.ui.toast('The Diff pane is in the desktop app; run `git diff` here')
}

/** Opens a file in the desktop's Files pane; where there is none, in the host's default app. */
async function openFile($: EngineInterface, path: string) {
  if (await showPane($, { pane: 'file', path })) return
  await openUrl($, path)
}

/** Opens a path or URL in the host's default app. */
async function openUrl($: EngineInterface, target: string) {
  for (const argv of [['open', target], ['xdg-open', target]]) {
    try {
      if ((await $.process.run(argv, { timeoutMs: 10_000 })).exitCode === 0) return
    } catch {
      // try the next opener
    }
  }
  $.ui.toast(`Cannot open ${target}`, { timeoutMs: 8000 })
}

/** Approves the artifact as it was on screen: if the file changed since, nothing is approved. */
async function approve($: EngineInterface, shown: Approval) {
  const fresh = await refresh($)
  const now = fresh && fresh.enabled ? fresh.stages[shown.stage] : undefined
  if (!now || now.sha256 !== shown.sha256) {
    $.ui.toast(`${shown.path} changed since it was shown. Review it again.`, { timeoutMs: 8000 })
    return
  }
  await press($, `sdlc approve ${shown.stage}`)
  const after = await read($, stateAtom)
  const recorded = after && after.enabled ? after.stages[shown.stage]?.approved_sha256 : null
  if (recorded && recorded !== shown.sha256) {
    $.ui.toast(
      `${shown.path} changed during approval: recorded ${short(recorded)}, shown ${short(shown.sha256)}`,
      { timeoutMs: 10_000 },
    )
  }
}

/** The review of the code as it is now: `ready`, `changes`, or `none` (never run, or the
 *  code changed since). */
function reviewState(s: On): 'ready' | 'changes' | 'none' {
  return s.review?.current ? s.review.verdict : 'none'
}

/** Whether every plan step is done; a plan without numbered steps counts as done when
 *  verify passes. */
const isBuilt = (s: On, steps: Steps) => (steps.total === 0 ? !!s.verify.last?.ok : steps.done >= steps.total)

/** The one action that moves the feature on from where it stands. */
function nextAction($: EngineInterface, s: On, steps: Steps = NO_STEPS): Action | null {
  if (!s.active) {
    if (s.fasttrack) return { key: 'fasttrack-off', label: 'End fast-track', run: () => press($, 'sdlc trivial off') }
    return { key: 'start', label: 'Start feature', run: () => runCommand($, '/sdlc:intent') }
  }
  if (s.stage === 'build') {
    const build = (label: string): Action => ({ key: 'build', label, run: () => runCommand($, '/sdlc:build') })
    if (!isBuilt(s, steps)) {
      if (steps.total === 0) return build('Build')
      return build(steps.done === 0 ? 'Start building' : 'Continue building')
    }
    // A finished build goes through review, then the PR; the feature closes once the PR exists.
    if (s.pr) return { key: 'done', label: 'Close feature', run: () => press($, 'sdlc done') }
    const review = reviewState(s)
    if (review === 'ready') return { key: 'pr', label: 'Create PR', run: () => runCommand($, '/sdlc:review', 'pr') }
    return {
      key: 'review',
      label: review === 'changes' ? 'Review again' : 'Review',
      run: () => runCommand($, '/sdlc:review'),
    }
  }
  const stage = s.stage
  const info = stage ? s.stages[stage] : undefined
  if (!stage || !info) return null
  if (info.state === 'missing') {
    // The plan is drafted in plan mode, read-only until the person accepts it; plan.md is
    // written after that, and only `sdlc approve plan` opens the code gate.
    if (stage === 'plan') {
      const task = `Write plan.md for feature \`${s.active}\` with the sdlc:plan skill.`
      return { key: 'write', label: 'Write plan', run: () => runCommand($, '/plan', task) }
    }
    return { key: 'write', label: `Write ${stage}`, run: () => runCommand($, `/sdlc:${stage}`) }
  }
  if ((info.state === 'draft' || info.state === 'stale') && info.sha256) {
    const sha256 = info.sha256
    return {
      key: 'approve',
      label: `${info.state === 'stale' ? 'Re-approve' : 'Approve'} ${stage}`,
      run: () => approve($, { stage, path: info.path, sha256 }),
    }
  }
  return null
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    try {
      await $.tool.register(UNLOCK_TOOL_SPEC)
    } catch {
      // an engine without plugin tools: the band simply never offers the request
    }
    await refresh($)
    // The timer catches edits made outside the session (the person's editor). Where
    // SDLC is off it idles; prompts and tool calls still notice a later /sdlc:init.
    $.clock.every(REFRESH_MS, () =>
      void read($, stateAtom).then(s => (s && !s.enabled ? undefined : refresh($))),
    )
    return started
  })

  on('prompt.submit', async ($, e, next) => {
    const result = await next(e)
    void refresh($)
    return result
  })

  on('tool.call', async ($, e, next) => {
    if (e.tool === `mcp__${$.plugin.name}__${UNLOCK_TOOL}`) {
      const s = await refresh($)
      if (!s || !s.enabled || !s.tests_locked) return { result: 'The test files are not locked; nothing to ask.' }
      const reason = String((e as { reason?: unknown }).reason ?? '').trim() || 'no reason given'
      await update($, unlockAtom, () => ({ reason }))
      await update($, hiddenAtom, () => false)
      return {
        result:
          'The SDLC bar now asks the user to unlock the tests. Stop here and wait: tests stay ' +
          'read-only until the [SDLC] status no longer says they are locked.',
      }
    }
    const ran = await next(e)
    if (['Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Bash'].includes(e.tool)) void refresh($)
    return ran
  })

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    void refresh($)
    return result
  })

  // The prompt footer: the session modes as the engine draws them, then `SDLC`,
  // which unfolds the band above the prompt into the details and folds it back.
  on('ui.render', { component: 'SessionMode' }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const modes = e.props.modes
    return (
      <Box gap={1}>
        {modes.length > 0 ? <Text dimColor>{modes.join(' & ')}</Text> : null}
        <Button
          key="sdlc"
          label="SDLC"
          plain
          onPress={() => toggleDetails($)}
        />
      </Box>
    )
  })

  // The band: one row (feature, stage, status, next action), or the details.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const s = await read($, stateAtom)
    const error = await read($, errorAtom)
    const isOpen = await read($, detailsAtom)
    if (!isOpen && (!s || !s.enabled || (await read($, hiddenAtom)))) return next(e)

    const els = {
      ...($.ui.resolve(e) as Elements),
      surface: e.surface,
      columns: e.props.bodyColumns,
      isWorking: e.props.isWorking,
    }
    const busy = await read($, busyAtom)
    const files = await read($, filesAtom)
    const steps = files.steps
    const unlock = await read($, unlockAtom)
    if (!isOpen && s && s.enabled) {
      return (
        <els.Box gap={1}>
          {Summary(els, $, s, steps)}
          {ActionRow(els, $, s, busy, steps, unlock)}
          <els.Button key="hide" label="×" role="dismiss" onPress={() => update($, hiddenAtom, () => true)} />
        </els.Box>
      )
    }
    return Details(els, $, s, error, busy, files, unlock)
  })
}

// ---------------------------------------------------------------------- drawing

// The element constructors come from `$.ui.resolve(e)`; each part takes them as its
// first argument so one tree draws on every surface. The desktop gets vector bars and
// rules, as its own popovers draw them; the terminal gets text.
type Elements = { Box: any; Text: any; Button: any; Markdown: any; Svg?: any }
type Els = Elements & { surface: string; columns: number; isWorking: boolean }

/** Actions that record a decision about a file: held while Claude's turn runs, since the
 *  file may still be half written (a typed `sdlc approve` waits for the turn the same way). */
const HELD_WHILE_WORKING = new Set(['approve', 'done'])
const isHeld = (els: Els, key: string) => els.isWorking && HELD_WHILE_WORKING.has(key)

/** A button that reads as disabled (the API has no `disabled`): dim, and a press only says
 *  why. Otherwise the button runs `run`, once at a time. */
function ActionButton(els: Els, $: EngineInterface, a: { key: string; label: string; run: () => unknown },
  hotkey: string, isPrimary: boolean, busy: boolean) {
  const { Button } = els
  if (isHeld(els, a.key)) {
    return (
      <Button key={a.key} label={a.label} dimColor
        onPress={() => $.ui.toast('Available when Claude finishes the current turn')} />
    )
  }
  return (
    <Button
      key={a.key}
      label={busy && a.key === 'approve' ? 'Approving…' : a.label}
      {...keys(els, hotkey, isPrimary)}
      onPress={() => (busy ? undefined : a.run())}
    />
  )
}

/** A hotkey and the accent where they help: the terminal. The desktop draws its own quiet
 *  buttons, as it draws Create PR: no accent and no key badge. */
const keys = (els: Els, hotkey: string, isPrimary = false) =>
  els.surface === 'terminal'
    ? { ...(hotkey ? { hotkey } : {}), variant: isPrimary ? ('primary' as const) : undefined }
    : {}

const STAGE_NAME: Record<Stage | 'build', string> = { intent: 'Intent', spec: 'Spec', plan: 'Plan', build: 'Build' }
const STATE_WORD: Record<string, string> = {
  approved: 'Approved',
  skipped: 'Skipped',
  draft: 'Draft',
  stale: 'Changed',
  missing: 'Not started',
}
const ACCENT = '#5b8def'
const TRACK = 'rgba(128,128,128,0.28)'

/** Where the active feature stands, in words: "Spec · waiting for approval". */
function whereText(s: On, steps: Steps = NO_STEPS): string {
  if (!s.active) return s.fasttrack ? 'Fast-track on' : 'No active feature'
  if (s.stage === 'build' && isBuilt(s, steps)) {
    if (s.pr) return 'Build · PR created'
    if (reviewState(s) === 'ready') return 'Build · review passed'
  }
  if (s.stage === 'build' && steps.total > 0) return `Build · ${steps.done}/${steps.total} steps`
  if (s.stage === 'build') return `Build · ${s.verify.last?.ok ? 'verify passed' : 'in progress'}`
  const doing = s.stage_state === 'missing' ? 'not started'
    : s.stage_state === 'stale' ? 'changed after approval' : 'waiting for approval'
  return `${STAGE_NAME[s.stage ?? 'intent']} · ${doing}`
}

/** The band's one row: feature, where it stands (the current artifact a link that opens
 *  it in the Files pane), a warning when one applies. */
function Summary(els: Els, $: EngineInterface, s: On, steps: Steps) {
  const { Box, Text } = els
  // One or two words: a longer note pushes the band's button onto a second line, and the
  // reason for an unlock request is already in the chat.
  const warn = s.verify.last && !s.verify.last.ok ? 'verify failed'
    : s.stage === 'build' && !s.pr && reviewState(s) === 'changes' ? 'changes required'
    : s.tests_locked ? 'tests locked' : ''
  const info = s.stage && s.stage !== 'build' ? s.stages[s.stage] : undefined
  const isFile = !!info && (info.state === 'draft' || info.state === 'stale')
  const where = whereText(s, steps)
  return (
    <Box gap={1} flexShrink={1} flexGrow={1}>
      {s.active ? TitleButton(els, $, s.active) : null}
      {isFile && info ? FileName(els, $, s.root, info.path, true) : null}
      <Text dimColor wrap="truncate-end">
        {isFile ? where.slice(where.indexOf('·')) : where}
      </Text>
      {warn ? (
        <Text color="red" wrap="truncate-end">
          {warn}
        </Text>
      ) : null}
    </Box>
  )
}

/** The one button for the next action; it runs at once. */
function ActionRow(els: Els, $: EngineInterface, s: On, busy: boolean, steps: Steps, unlock: UnlockRequest) {
  const { Box } = els
  // Claude asked for the locked tests to be unlocked: that is what the build waits on now.
  // Otherwise a lock is part of the plan (bug-fix flow) and the next build action leads.
  const action = unlock && s.tests_locked
    ? { key: 'unlock', label: 'Unlock tests', run: () => press($, 'sdlc unlock tests') }
    : nextAction($, s, steps)
  // With no feature there are two ways in: a feature, or a small change on fast-track.
  const list = [action, !s.active && !s.fasttrack ? fastTrackAction($) : null].filter((a): a is Action => !!a)
  if (list.length === 0) return null
  return <Box gap={1}>{list.map((a, i) => ActionButton(els, $, a, i === 0 ? 'a' : 't', i === 0, busy))}</Box>
}

/** A small change without intent, spec and plan: the person types what it is. */
function fastTrackAction($: EngineInterface): Action {
  return {
    key: 'fasttrack',
    label: 'Fast-track',
    run: () => fillCommand($, 'sdlc trivial', 'Type what the small change is, then press Enter'),
  }
}

/** A section's title: bold, in the text colour, so it reads as the head of its table
 *  rather than as one more grey value. */
function SectionTitle({ Text }: Els, title: string) {
  return (
    <Text key={`title-${title}`} bold>
      {title}
    </Text>
  )
}

/** A thin rule across the body. */
function Rule({ Svg, Text, surface, columns }: Els, key: string) {
  if (surface === 'desktop' && Svg) {
    return (
      <Svg
        key={key}
        alt=""
        source={`<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1" viewBox="0 0 1000 1"><rect width="1000" height="1" fill="${TRACK}"/></svg>`}
      />
    )
  }
  return <Text dimColor>{'─'.repeat(Math.max(8, Math.min(columns, 80)))}</Text>
}

/** Four segments, intent to build: done in the accent, the current one faint, the rest track. */
function StageBar({ Svg, Text, surface }: Els, s: On) {
  const levels = [...STAGES, 'build' as const].map(stage => {
    const isCurrent = s.stage === stage
    if (stage === 'build') return isCurrent ? (s.verify.last?.ok ? 1 : 0.4) : 0
    const state = s.stages[stage]?.state
    return state === 'approved' || state === 'skipped' ? 1 : isCurrent ? 0.4 : 0
  })
  const alt = `Stages: ${[...STAGES, 'build'].map((st, i) => `${st} ${levels[i] === 1 ? 'done' : levels[i] ? 'current' : 'to do'}`).join(', ')}`
  if (surface === 'desktop' && Svg) {
    const W = 1000
    const GAP = 10
    const w = (W - GAP * 3) / 4
    const rects = levels
      .map((level, i) => {
        const x = i * (w + GAP)
        const fill = level ? ACCENT : TRACK
        const opacity = level === 1 || !level ? 1 : level
        return `<rect x="${x}" y="0" width="${w}" height="6" rx="3" fill="${fill}" fill-opacity="${opacity}"/>`
      })
      .join('')
    return (
      <Svg
        key="stages"
        alt={alt}
        source={`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="6" viewBox="0 0 ${W} 6">${rects}</svg>`}
      />
    )
  }
  return (
    <Text>
      {[...STAGES, 'build' as const].map((stage, i) => (
        <Text key={stage} dimColor={!levels[i]} bold={levels[i] === 0.4}>
          {i ? '  ›  ' : ''}
          {STAGE_NAME[stage]}
        </Text>
      ))}
    </Text>
  )
}

/** The title row: the title (it folds the details) at the left, the close mark at the right. */
function Head(els: Els, $: EngineInterface, title: string, close: unknown) {
  return (
    <els.Box key="head" justifyContent="space-between" gap={2}>
      {TitleButton(els, $, title)}
      {close}
    </els.Box>
  )
}

/** The feature's name as a button that folds and unfolds the details, as SDLC in the footer
 *  does. `plain` draws it as text, with a plate only under the pointer. */
function TitleButton({ Box, Button, surface }: Els, $: EngineInterface, title: string) {
  const button = <Button key="feature" label={title} plain onPress={() => toggleDetails($)} />
  // The desktop pads a button for its hover plate; pull it back so the name lines up with
  // the text under it. The terminal draws a plain button with no padding.
  return surface === 'desktop' ? <Box key="feature-box" marginLeft={TITLE_INSET}>{button}</Box> : button
}

/** The desktop's horizontal padding of a plain button, in cells (about 6 px). */
const TITLE_INSET = -0.75

/** One row of a section's table: what, its state, and the action that applies to it. */
type Line = { key: string; label: unknown; labelText: string; value: unknown; valueText: string; action?: unknown }

const ACTION_WIDTH = 12

/** A titled table. Its label and value columns are as wide as their longest entry, so the
 *  states line up; `minWidth` lets sections sit side by side and wrap when room runs out. */
function Section(els: Els, key: string, title: string, lines: Line[]) {
  const { Box, Text } = els
  const labelWidth = Math.max(...lines.map(l => l.labelText.length)) + 3
  const valueWidth = Math.max(...lines.map(l => l.valueText.length)) + 3
  const hasActions = lines.some(l => l.action)
  return (
    <Box key={key} flexDirection="column" flexShrink={0}
      minWidth={labelWidth + valueWidth + (hasActions ? ACTION_WIDTH : 0)}>
      {SectionTitle(els, title)}
      {lines.map(l => (
        <Box key={l.key}>
          <Box width={labelWidth} flexShrink={0}>
            {typeof l.label === 'string' ? <Text wrap="truncate-end">{l.label}</Text> : l.label}
          </Box>
          <Box width={valueWidth} flexShrink={0}>
            {typeof l.value === 'string' ? <Text dimColor wrap="truncate-end">{l.value}</Text> : l.value}
          </Box>
          {l.action ?? null}
        </Box>
      ))}
    </Box>
  )
}

/** A file name: a link that opens it when it exists, quiet text when it does not. */
function FileName({ Markdown, Text }: Els, $: EngineInterface, root: string, rel: string, exists: boolean) {
  const name = rel.split('/').pop() ?? rel
  if (!exists) return <Text dimColor>{name}</Text>
  return (
    <Markdown
      key={`open-${name}`}
      text={`[${name}](${fileUrl(root, rel)})`}
      onLinkPress={() => void openFile($, `${root}/${rel}`)}
    />
  )
}

/** An artifact's state in one word; who approved or why it was skipped is in approvals.json. */
function stageValueText(info: StageInfo | undefined): string {
  const state = info?.state ?? 'missing'
  return STATE_WORD[state] ?? state
}

/** The unfolded band, laid out as the desktop's own popovers are: Artifacts and Status
 *  side by side where the band is wide enough, one under the other where it is not. */
function Details(
  els: Els,
  $: EngineInterface,
  s: SdlcState | null,
  error: string | null,
  busy: boolean,
  files: ExtraFiles,
  unlock: UnlockRequest = null,
) {
  const { Box, Text, Button } = els
  const close = (
    <Button key="close" label="×" role="dismiss" onPress={() => update($, detailsAtom, () => false)} />
  )
  if (error) {
    return (
      <Box flexDirection="column" gap={1}>
        {Head(els, $, 'SDLC', close)}
        <Text color="red">{error}</Text>
        <Box>
          <Button key="retry" label="Retry" {...keys(els, 'r')} onPress={() => void refresh($)} />
        </Box>
      </Box>
    )
  }
  if (!s) return <Text dimColor>Reading SDLC state…</Text>
  if (!s.enabled) {
    return (
      <Box flexDirection="column" gap={1}>
        {Head(els, $, 'SDLC', close)}
        <Text dimColor>Not enabled in this repository. Init detects the verify commands and adds</Text>
        <Text dimColor>sdlc.config.json, a CLAUDE.md section, REVIEW.md and docs/sdlc/.</Text>
        <Box>
          <Button key="init" label="Init SDLC" {...keys(els, 'i', true)} onPress={() => runCommand($, '/sdlc:init')} />
        </Box>
      </Box>
    )
  }

  const dir = featureDir(s)
  const action = nextAction($, s, files.steps)
  const button = (key: string, label: string, run: () => unknown, hotkey: string, isPrimary = false) => (
    <Button key={key} label={label} {...keys(els, hotkey, isPrimary)} onPress={() => (busy ? undefined : run())} />
  )

  const artifacts: Line[] = STAGES.map(stage => {
    const info = s.stages[stage]
    const exists = !!info && info.state !== 'missing' && info.state !== 'skipped'
    const valueText = stageValueText(info)
    return {
      key: stage,
      label: FileName(els, $, s.root, info?.path ?? `${dir}/${stage}.md`, exists),
      labelText: `${stage}.md`,
      value: info?.state === 'stale' ? <Text color="red">{valueText}</Text> : valueText,
      valueText,
    }
  })
  if (files.progress) {
    artifacts.push({ key: 'progress', label: FileName(els, $, s.root, `${dir}/progress.md`, true),
      labelText: 'progress.md', value: 'Build log', valueText: 'Build log' })
  }
  if (files.approvals) {
    artifacts.push({ key: 'approvals', label: FileName(els, $, s.root, `${dir}/approvals.json`, true),
      labelText: 'approvals.json', value: 'Audit trail', valueText: 'Audit trail' })
  }

  const last = s.verify.last
  const verify = s.verify.commands.length === 0 ? 'Not configured'
    : last ? `${last.ok ? 'Passed' : 'Failed'} ${clock(last.at).slice(11)}` : 'Not run yet'
  const gate = s.code_gate === 'open' ? 'Open' : 'Closed'
  const progressText = files.steps.total > 0 && s.stage === 'build'
    ? `${files.steps.done}/${files.steps.total} steps`
    : null
  const status: Line[] = [
    ...(progressText
      ? [{ key: 'building', label: 'Building', labelText: 'Building', value: progressText, valueText: progressText }]
      : []),
    { key: 'gate', label: 'Code gate', labelText: 'Code gate', value: gate, valueText: gate },
    { key: 'verify', label: 'Verify', labelText: 'Verify',
      value: last && !last.ok ? <Text color="red">{verify}</Text> : verify, valueText: verify },
  ]
  if (s.stage === 'build') {
    const review = reviewState(s)
    const reviewText = review === 'ready' ? 'Passed' : review === 'changes' ? 'Changes required'
      : s.review ? 'Out of date' : 'Not run'
    status.push({ key: 'review', label: 'Review', labelText: 'Review',
      value: review === 'changes' ? <Text color="red">{reviewText}</Text> : reviewText, valueText: reviewText })
  }
  if (s.pr) {
    const url = s.pr.url
    const prText = `#${url.split('/').pop() ?? ''}`
    status.push({ key: 'pr', label: 'PR', labelText: 'PR', valueText: prText,
      value: <els.Markdown key="pr-link" text={`[${prText}](${url})`} onLinkPress={() => void openUrl($, url)} /> })
  }
  if (s.fasttrack) {
    status.push({ key: 'fasttrack', label: 'Fast-track', labelText: 'Fast-track', value: 'On', valueText: 'On' })
  }
  if (s.tests_locked) {
    // A short value keeps the three sections side by side; red marks Claude's unlock request.
    status.push({ key: 'tests', label: 'Tests', labelText: 'Tests',
      value: unlock ? <Text color="red">Locked</Text> : 'Locked', valueText: 'Locked' })
  }

  // What the person can do now, one button per line.
  const actions: { key: string; label: string; run: () => unknown; hotkey: string }[] = []
  if (action) {
    const label = busy && action.key === 'approve' ? 'Approving…' : action.label
    actions.push({ key: action.key, label, run: action.run, hotkey: 'a' })
  }
  const verified = !!s.verify.last?.ok
  const steps = files.steps
  const building = s.active && s.stage === 'build'
  // With numbered plan steps the build's progress decides; without, the verify result does.
  const started = steps.total === 0 || steps.done > 0
  const finished = isBuilt(s, steps)
  if (building && started && !verified) {
    actions.push({ key: 'verify', label: 'Run verify', hotkey: 'v', run: () => runCommand($, '/sdlc:verify') })
  }
  if (building && started) {
    actions.push({ key: 'changes', label: 'Show changes', hotkey: 'd', run: () => showChanges($) })
  }
  if (building && started && (steps.total > 0 || verified) && action?.key !== 'review') {
    actions.push({ key: 'review', label: 'Review', hotkey: 'r', run: () => runCommand($, '/sdlc:review') })
  }
  if (building && finished && action?.key !== 'done') {
    actions.push({ key: 'done', label: 'Close feature', hotkey: 'c', run: () => press($, 'sdlc done') })
  }
  if (s.tests_locked) actions.push({ key: 'unlock', label: 'Unlock tests', run: () => press($, 'sdlc unlock tests'), hotkey: 'u' })
  if (s.fasttrack && action?.key !== 'fasttrack-off') {
    actions.push({ key: 'fasttrack-end', label: 'End fast-track', run: () => press($, 'sdlc trivial off'), hotkey: 'f' })
  }
  if (!s.active && !s.fasttrack) actions.push({ ...fastTrackAction($), hotkey: 't' })
  const others: Line[] = s.features
    .filter(f => f.slug !== s.active && !f.done)
    .map(f => {
      const valueText = `${STAGE_NAME[f.stage]}${f.stage_state ? ` · ${STATE_WORD[f.stage_state] ?? f.stage_state}` : ''}`
      return { key: f.slug, label: f.slug, labelText: f.slug, value: valueText, valueText,
        action: button(`activate-${f.slug}`, 'Make active', () => press($, `sdlc feature ${f.slug}`), '') }
    })

  return (
    <Box flexDirection="column" gap={1}>
      <Box flexDirection="column">
        {Head(els, $, s.active ?? 'SDLC', close)}
        <Text dimColor>{whereText(s, files.steps)}</Text>
      </Box>
      {s.active ? StageBar(els, s) : null}
      {Rule(els, 'rule')}
      <Box flexWrap="wrap" columnGap={6} rowGap={1}>
        {s.active ? Section(els, 'artifacts', 'Artifacts', artifacts) : null}
        {Section(els, 'status', 'Status', status)}
        {actions.length > 0 ? (
          // A column of buttons. The desktop lays a row out a whole line tall, so the buttons
          // stand half a row apart there; the terminal's bracketed buttons need no gap.
          <Box key="actions" flexDirection="column" flexShrink={0}
            minWidth={Math.max(...actions.map(a => a.label.length)) + 6}>
            {SectionTitle(els, 'Actions')}
            <Box flexDirection="column" alignItems="flex-start" rowGap={els.surface === 'desktop' ? 0.5 : 0}>
              {actions.map((a, i) => ActionButton(els, $, a, a.hotkey, i === 0, busy))}
            </Box>
          </Box>
        ) : null}
        {others.length > 0 ? Section(els, 'others', 'Other features', others) : null}
      </Box>
    </Box>
  )
}
