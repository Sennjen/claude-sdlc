import { expect, mock, test } from 'claude-code/testing'
import type { Engine, Mounted } from 'claude-code/testing'
import type { On, RenderComponent } from 'claude-code'

import type { SdlcState, StageInfo } from '../types'

type On1 = Extract<SdlcState, { enabled: true }>

const SHA = 'b'.repeat(64)
const stage = (over: Partial<StageInfo>): StageInfo => ({
  state: 'missing', path: '', sha256: null, approved_sha256: null, by: null, at: null, reason: null, ...over,
})
const DRAFT: On1 = {
  enabled: true,
  root: '/repo',
  active: 'feat-a',
  stage: 'spec',
  stage_state: 'draft',
  stage_label: '2 Design (spec.md)',
  next_skill: 'sdlc:spec',
  code_gate: 'closed',
  artifact: 'docs/sdlc/feat-a/spec.md',
  stages: {
    intent: stage({ state: 'approved', path: 'docs/sdlc/feat-a/intent.md', sha256: 'a'.repeat(64),
      approved_sha256: 'a'.repeat(64), by: 'Tester', at: '2026-10-05T12:00:00+03:00' }),
    spec: stage({ state: 'draft', path: 'docs/sdlc/feat-a/spec.md', sha256: SHA }),
    plan: stage({ path: 'docs/sdlc/feat-a/plan.md' }),
  },
  features: [{ slug: 'feat-a', stage: 'spec', stage_state: 'draft', done: false }],
  tests_locked: false,
  fasttrack: null,
  verify: { commands: ['npm test'], last: null },
  review: null,
  pr: null,
}
const BUILD: On1 = {
  ...DRAFT,
  stage: 'build',
  stage_state: null,
  code_gate: 'open',
  stages: {
    ...DRAFT.stages,
    spec: { ...(DRAFT.stages.spec as StageInfo), state: 'approved', approved_sha256: SHA },
    plan: stage({ state: 'approved', path: 'docs/sdlc/feat-a/plan.md', sha256: 'c'.repeat(64),
      approved_sha256: 'c'.repeat(64) }),
  },
}

/** Stands in for sdlc.py and the host: `cli state` prints `state`, `hook prompt` records. */
function fakeSdlc(on: On, state: SdlcState = DRAFT) {
  const fake = {
    state: state as On1,
    commands: [] as string[],
    toasts: [] as string[],
    filled: [] as string[],
    opened: [] as string[],
    closed: [] as string[],
    opens: [] as string[],
    shown: [] as string[],
    panes: [] as string[],
    ran: [] as string[],
    canRun: true,
    planText: null as string | null,
    progressText: null as string | null,
    hasFilesPane: true,
    clock: mock.clock(on),
    tools: [] as string[],
  }
  on('tool.register', ($, e) => {
    fake.tools.push(e.name)
    return { value: { tool: `mcp__sdlc__${e.name}` } }
  })
  // A slash command run as if typed; an unknown one is refused.
  on('command.run', ($, e) => {
    if (!fake.canRun) throw new Error(`unknown command /${e.command}`)
    fake.ran.push(e.args ? `${e.command} ${e.args}` : e.command)
    return { text: '' }
  })
  // The desktop's view server: show_pane opens a file in its Files pane.
  on('mcp.call', ($, e) => {
    if (!fake.hasFilesPane) throw new Error('no such server: ccd_view')
    if (e.server === 'ccd_view' && e.tool === 'show_pane') {
      if (e.args.pane === 'file') fake.shown.push(e.args.path as string)
      else fake.panes.push(e.args.pane as string)
    }
    return { value: { content: [], isError: false } }
  })
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'sid-1' }))
  on('session.cwd', () => ({ value: '/repo' }))
  on('ui.toast', ($, e) => {
    fake.toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.panes', () => ({ value: [] }))
  on('ui.open', ($, e) => {
    fake.opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('ui.close', ($, e) => {
    fake.closed.push(e.id)
    return { value: undefined }
  })
  on('prompt.fill', ($, e) => {
    fake.filled.push(e.text)
    return { isFilled: true, text: e.text, cursor: e.text.length }
  })
  on('fs.stat', () => {
    throw new Error('ENOENT')
  })
  // plan.md and progress.md as the build reads them; absent unless a test sets them.
  on('fs.read', ($, e) => {
    const text = e.path.endsWith('/plan.md') ? fake.planText : e.path.endsWith('/progress.md') ? fake.progressText : null
    if (text === null) throw new Error('ENOENT')
    return { value: text }
  })
  on('process.run', ($, e) => {
    const out = (stdout: string) => ({
      value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    })
    if (e.argv.includes('state')) return out(JSON.stringify(fake.state))
    if (e.argv[0] === 'open') {
      fake.opens.push(e.argv[1] as string)
      return out('')
    }
    const prompt = JSON.parse(e.init?.stdin ?? '{}').prompt as string
    fake.commands.push(prompt)
    const spec = fake.state.stages.spec as StageInfo
    fake.state = {
      ...fake.state,
      stage: 'plan',
      stage_state: 'missing',
      stages: { ...fake.state.stages, spec: { ...spec, state: 'approved', approved_sha256: spec.sha256, by: 'Tester' } },
    }
    return out(JSON.stringify({ hookSpecificOutput: {
      additionalContext: '[SDLC] The user (Tester) APPROVED spec.md of `feat-a` (sha256 bbbbbbbbbbbb). Next: plan.\n[SDLC] status',
    } }))
  })
  return fake
}

const SITE = { scroll: { offset: 0, bodyRows: 20 }, view: {} }

async function mount($: Engine, surface: 'terminal' | 'desktop', component: RenderComponent) {
  await $.session.start({ cwd: '/repo', surface, isInteractive: true })
  const props = {
    AbovePrompt: { hasSurvey: false, isWorking: false, maxRows: 20, bodyColumns: 120, ...SITE },
    Pane: { title: 'SDLC', isFocused: true, bodyColumns: 80, placement: 'inline', ...SITE },
    SessionMode: { modes: ['focus'] },
  }[component as 'AbovePrompt' | 'Pane' | 'SessionMode']
  const requestId = component === 'Pane' ? 'sdlc' : undefined
  return $.ui.mount({ plugin: 'sdlc', surface, component, props, requestId } as never) as Promise<
    Mounted<'terminal' | 'desktop'>
  >
}

/** Unfolds the details with SDLC in the footer; the keys of the action buttons drawn. */
async function details($: Engine, surface: 'terminal' | 'desktop') {
  const footer = await mount($, surface, 'SessionMode')
  const band = await mount($, surface, 'AbovePrompt')
  await footer.press({ key: 'sdlc' })
  const actionKeys = async () =>
    (await band.findAll({ type: 'Button' })).map(b => b.key).filter(k => k !== 'close' && k !== 'feature' && !k?.startsWith('activate-'))
  return { band, actionKeys }
}

const PLAN = `# Plan

## Approach
1. [ ] not a step: outside the work sequence

## Work sequence
1. [ ] Write the failing tests — verify: they fail
2. [ ] Implement — verify: they pass
3. [ ] Run verify — verify: all green

## Risks
`

const DONE_ALL = '- [x] Step 1: a\n- [x] Step 2: b\n- [X] step 3: c\n- [x] Step 9: not in the plan\n'
const VERIFIED: On1 ={ ...BUILD, verify: { commands: ['npm test'], last: { ok: true, at: '2026-10-05T18:47:00+02:00', report: null } } }
const IDLE: On1 = { ...DRAFT, active: null, stage: null, stage_state: null, stage_label: null, next_skill: null,
  artifact: null, stages: {}, features: [] }

for (const surface of ['terminal', 'desktop'] as const) {
  test(`${surface}: actions at spec: approve only`, async ($, on) => {
    fakeSdlc(on)
    const { actionKeys } = await details($, surface)
    expect(await actionKeys()).toEqual(['approve'])
  })

  test(`${surface}: actions in build before verify: build, run verify, show changes`, async ($, on) => {
    const fake = fakeSdlc(on, BUILD)
    const { band, actionKeys } = await details($, surface)
    expect(await actionKeys()).toEqual(['build', 'verify', 'changes'])

    await band.press({ key: 'changes' })
    expect(fake.panes).toEqual(['diff'])
    await band.press({ key: 'verify' })
    expect(fake.ran).toEqual(['sdlc:verify'])
    expect(fake.filled).toEqual([])
  })

  test(`${surface}: actions in build after verify: review, show changes, close feature`, async ($, on) => {
    const fake = fakeSdlc(on, VERIFIED)
    const { band, actionKeys } = await details($, surface)
    expect(await actionKeys()).toEqual(['review', 'changes', 'done'])

    await band.press({ key: 'done' })
    expect(fake.commands).toEqual(['sdlc done'])
  })

  test(`${surface}: locked tests alone keep the build action; Claude's request brings Unlock`, async ($, on) => {
    const fake = fakeSdlc(on, { ...BUILD, tests_locked: true })
    fake.planText = PLAN
    fake.progressText = '- [x] Step 1: tests written\n'
    const band = await mount($, surface, 'AbovePrompt')
    expect(fake.tools).toEqual(['request_test_unlock'])
    // Locked as the bug-fix flow plans it: the build goes on.
    expect((await band.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['feature', 'build', 'hide'])

    const asked = await $.tool.call({ tool: 'mcp__sdlc__request_test_unlock', reason: 'review found new cases' } as never)
    expect(JSON.stringify(asked)).toContain('Stop here and wait')
    expect((await band.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['feature', 'unlock', 'hide'])
    // The reason stays in the chat: the band keeps a short note, so its button stays on the row.
    expect(await band.find({ text: /review found new cases/ })).toBeUndefined()
    expect(await band.find({ text: /^tests locked$/ })).toBeDefined()

    await band.press({ key: 'unlock' })
    expect(fake.commands).toEqual(['sdlc unlock tests'])
  })

  test(`${surface}: asking for an unlock with nothing locked changes nothing`, async ($, on) => {
    fakeSdlc(on, BUILD)
    const band = await mount($, surface, 'AbovePrompt')
    const asked = await $.tool.call({ tool: 'mcp__sdlc__request_test_unlock', reason: 'x' } as never)
    expect(JSON.stringify(asked)).toContain('not locked')
    expect(await band.find({ key: 'unlock' })).toBeUndefined()
  })

  test(`${surface}: an approved plan shows 0/N steps and offers only Start building`, async ($, on) => {
    const fake = fakeSdlc(on, BUILD)
    fake.planText = PLAN
    const { band, actionKeys } = await details($, surface)

    expect(await band.find({ text: /Build · 0\/3 steps/ })).toBeDefined()
    expect(await band.find({ text: /^0\/3 steps$/ })).toBeDefined()
    expect(await actionKeys()).toEqual(['build'])
    expect(JSON.stringify((await band.find({ key: 'actions' }))?.children)).toContain('Start building')
  })

  test(`${surface}: some steps done: continue, review, show changes; no close yet`, async ($, on) => {
    const fake = fakeSdlc(on, BUILD)
    fake.planText = PLAN
    fake.progressText = '- [x] Step 1: tests written\n- step 2 started\n'
    const { band, actionKeys } = await details($, surface)

    expect(await band.find({ text: /^1\/3 steps$/ })).toBeDefined()
    expect(await actionKeys()).toEqual(['build', 'verify', 'changes', 'review'])
    expect(JSON.stringify((await band.find({ key: 'actions' }))?.children)).toContain('Continue building')
  })

  test(`${surface}: all steps done: the band offers Review, the details keep Close feature`, async ($, on) => {
    const fake = fakeSdlc(on, { ...BUILD, tests_locked: true })
    fake.planText = PLAN
    fake.progressText = DONE_ALL
    const band = await mount($, surface, 'AbovePrompt')
    expect((await band.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['feature', 'review', 'hide'])

    const open = await details($, surface)
    expect(await open.band.find({ text: /^3\/3 steps$/ })).toBeDefined()
    expect(await open.band.find({ text: /^Not run$/ })).toBeDefined()
    const actionKeys = open.actionKeys
    expect(await actionKeys()).toEqual(['review', 'verify', 'changes', 'done', 'unlock'])
  })

  test(`${surface}: after the build the band leads through Review, Create PR, Close feature`, async ($, on) => {
    const fake = fakeSdlc(on, BUILD)
    fake.planText = PLAN
    fake.progressText = DONE_ALL
    const band = await mount($, surface, 'AbovePrompt')
    const keys = async () => (await band.findAll({ type: 'Button' })).map(b => b.key)

    await band.press({ key: 'review' })
    expect(fake.ran).toEqual(['sdlc:review'])

    // The reviewer asked for changes: review again, with a short warning.
    fake.state = { ...fake.state, review: { verdict: 'changes', at: '2026-10-06T10:00:00+02:00', current: true } }
    await fake.clock.advance(10_000)
    expect(await keys()).toEqual(['feature', 'review', 'hide'])
    expect((await band.find({ key: 'review' }))?.props.label).toBe('Review again')
    expect(await band.find({ text: /^changes required$/ })).toBeDefined()
    expect(fake.toasts).toContain('SDLC review: changes required')

    // A passed review of the code as it is now: Create PR runs the review skill's PR step.
    fake.state = { ...fake.state, review: { verdict: 'ready', at: '2026-10-06T11:00:00+02:00', current: true } }
    await fake.clock.advance(10_000)
    expect(await keys()).toEqual(['feature', 'pr', 'hide'])
    expect(await band.find({ text: /Build · review passed/ })).toBeDefined()
    await band.press({ key: 'pr' })
    expect(fake.ran).toEqual(['sdlc:review', 'sdlc:review pr'])

    // Once the PR exists, the band offers Close feature.
    fake.state = { ...fake.state, pr: { url: 'https://github.com/o/r/pull/12', at: '2026-10-06T11:05:00+02:00' } }
    await fake.clock.advance(10_000)
    expect(await keys()).toEqual(['feature', 'done', 'hide'])
    expect(await band.find({ text: /Build · PR created/ })).toBeDefined()
    await band.press({ key: 'done' })
    expect(fake.commands).toEqual(['sdlc done'])
  })

  test(`${surface}: a review of older code does not open Create PR`, async ($, on) => {
    const fake = fakeSdlc(on, { ...VERIFIED, review: { verdict: 'ready', at: '2026-10-06T11:00:00+02:00', current: false } })
    const { band, actionKeys } = await details($, surface)
    expect(await actionKeys()).toEqual(['review', 'changes', 'done'])
    expect(await band.find({ text: /^Out of date$/ })).toBeDefined()
    expect(fake.ran).toEqual([])
  })

  test(`${surface}: the details link the PR`, async ($, on) => {
    const fake = fakeSdlc(on, { ...VERIFIED, pr: { url: 'https://github.com/o/r/pull/12', at: null } })
    const { band } = await details($, surface)
    await band.press({ key: 'pr-link', link: { href: 'https://github.com/o/r/pull/12' } })
    expect(fake.opens).toEqual(['https://github.com/o/r/pull/12'])
  })

  test(`${surface}: with no feature the band offers Start feature and Fast-track`, async ($, on) => {
    const fake = fakeSdlc(on, IDLE)
    const band = await mount($, surface, 'AbovePrompt')
    expect((await band.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['start', 'fasttrack', 'hide'])
    // The summary takes the free room, so the buttons sit at the right, by the ×.
    const summary = (await band.drawn()) as { children: { props: { flexGrow?: number } }[] }
    expect(summary.children[0]?.props.flexGrow).toBe(1)

    await band.press({ key: 'fasttrack' })
    expect(fake.filled).toEqual(['sdlc trivial '])
  })

  test(`${surface}: actions with no feature: start feature, fast-track`, async ($, on) => {
    const fake = fakeSdlc(on, IDLE)
    const { band, actionKeys } = await details($, surface)
    expect(await actionKeys()).toEqual(['start', 'fasttrack'])

    await band.press({ key: 'fasttrack' })
    expect(fake.filled).toEqual(['sdlc trivial '])
  })

  test(`${surface}: the band shows the feature, stage, status and Approve`, async ($, on) => {
    const fake = fakeSdlc(on)
    const ui = await mount($, surface, 'AbovePrompt')

    expect(await ui.find({ text: /feat-a/ })).toBeDefined()
    expect(await ui.find({ key: 'open-spec.md' })).toBeDefined()
    expect(await ui.find({ text: /· waiting for approval/ })).toBeDefined()
    expect((await ui.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['feature', 'approve', 'hide'])
    // The desktop draws its own quiet button; the terminal keeps the hotkey and accent.
    const approve = await ui.find({ key: 'approve' })
    expect(approve?.props.hotkey).toBe(surface === 'terminal' ? 'a' : undefined)
    expect(approve?.props.variant).toBe(surface === 'terminal' ? 'primary' : undefined)

    await ui.press({ key: 'approve' })
    expect(fake.commands).toEqual(['sdlc approve spec'])
    expect(fake.toasts).toContain('The user (Tester) APPROVED spec.md of `feat-a` (sha256 bbbbbbbbbbbb)')
    expect(await ui.find({ key: 'approve' })).toBeUndefined()

    // The plan is drafted in plan mode: Write plan runs /plan with the sdlc:plan task.
    expect((await ui.find({ key: 'write' }))?.props.label).toBe('Write plan')
    await ui.press({ key: 'write' })
    expect(fake.ran).toEqual(['plan Write plan.md for feature `feat-a` with the sdlc:plan skill.'])
  })

  test(`${surface}: the current artifact in the band opens in the Files pane`, async ($, on) => {
    const fake = fakeSdlc(on)
    const ui = await mount($, surface, 'AbovePrompt')

    expect(await ui.find({ text: /· waiting for approval/ })).toBeDefined()
    await ui.press({ key: 'open-spec.md', link: { href: 'file:///repo/docs/sdlc/feat-a/spec.md' } })
    expect(fake.shown).toEqual(['/repo/docs/sdlc/feat-a/spec.md'])
  })

  test(`${surface}: without a Files pane the artifact opens in the default app`, async ($, on) => {
    const fake = fakeSdlc(on)
    fake.hasFilesPane = false
    const ui = await mount($, surface, 'AbovePrompt')

    await ui.press({ key: 'open-spec.md', link: { href: 'file:///repo/docs/sdlc/feat-a/spec.md' } })
    expect(fake.shown).toEqual([])
    expect(fake.opens).toEqual(['/repo/docs/sdlc/feat-a/spec.md'])
  })

  test(`${surface}: the feature's name, a plain button, folds and unfolds the details`, async ($, on) => {
    fakeSdlc(on)
    const band = await mount($, surface, 'AbovePrompt')
    const name = await band.find({ key: 'feature' })
    expect(name?.props.label).toBe('feat-a')
    expect(name?.props.plain).toBe(true)

    await band.press({ key: 'feature' })
    expect(await band.find({ key: 'open-intent.md' })).toBeDefined()
    await band.press({ key: 'feature' })
    expect(await band.find({ key: 'open-intent.md' })).toBeUndefined()
  })

  test(`${surface}: × hides the band until SDLC or a new status brings it back`, async ($, on) => {
    const fake = fakeSdlc(on)
    on('ui.render', () => ({ type: 'Box', props: {}, children: [] }))
    const footer = await mount($, surface, 'SessionMode')
    const band = await mount($, surface, 'AbovePrompt')
    expect((await band.find({ key: 'hide' }))?.props.role).toBe('dismiss')

    await band.press({ key: 'hide' })
    expect(await band.find({ key: 'approve' })).toBeUndefined()

    await footer.press({ key: 'sdlc' })
    expect(await band.find({ key: 'open-intent.md' })).toBeDefined()
    await band.press({ key: 'close' })
    expect(await band.find({ key: 'approve' })).toBeDefined()

    await band.press({ key: 'hide' })
    expect(await band.find({ key: 'approve' })).toBeUndefined()
    // spec gets approved elsewhere (typed in chat): the next refresh sees the new stage.
    const spec = fake.state.stages.spec as StageInfo
    fake.state = { ...fake.state, stage: 'plan', stage_state: 'missing',
      stages: { ...fake.state.stages, spec: { ...spec, state: 'approved', approved_sha256: spec.sha256 } } }
    await fake.clock.advance(10_000)
    expect(await band.find({ key: 'write' })).toBeDefined()
  })

  test(`${surface}: while Claude works, Approve waits for the turn to end`, async ($, on) => {
    const fake = fakeSdlc(on)
    await $.session.start({ cwd: '/repo', surface, isInteractive: true })
    const band = await $.ui.mount({
      plugin: 'sdlc', surface, component: 'AbovePrompt',
      props: { hasSurvey: false, isWorking: true, maxRows: 20, bodyColumns: 120, ...SITE },
    } as never) as Mounted<'terminal' | 'desktop'>

    // Drawn disabled: dim, and a press only explains.
    expect((await band.find({ key: 'approve' }))?.props.dimColor).toBe(true)
    await band.press({ key: 'approve' })
    expect(fake.commands).toEqual([])
    expect(fake.toasts).toContain('Available when Claude finishes the current turn')

    await band.redraw({ hasSurvey: false, isWorking: false, maxRows: 20, bodyColumns: 120, ...SITE } as never)
    expect((await band.find({ key: 'approve' }))?.props.dimColor).toBeUndefined()
    await band.press({ key: 'approve' })
    expect(fake.commands).toEqual(['sdlc approve spec'])
  })

  test(`${surface}: a file changed since it was shown is not approved`, async ($, on) => {
    const fake = fakeSdlc(on)
    const ui = await mount($, surface, 'AbovePrompt')

    const spec = fake.state.stages.spec as StageInfo
    fake.state = { ...fake.state, stages: { ...fake.state.stages, spec: { ...spec, sha256: 'd'.repeat(64) } } }
    await ui.press({ key: 'approve' })

    expect(fake.commands).toEqual([])
    expect(fake.toasts.join('\n')).toContain('changed since it was shown')
    expect(await ui.find({ key: 'approve' })).toBeDefined()
  })

  test(`${surface}: in build the band offers Build, which runs /sdlc:build at once`, async ($, on) => {
    const fake = fakeSdlc(on, BUILD)
    const ui = await mount($, surface, 'AbovePrompt')

    await ui.press({ key: 'build' })
    expect(fake.ran).toEqual(['sdlc:build'])
    expect(fake.filled).toEqual([])
    expect(fake.commands).toEqual([])
  })

  test(`${surface}: a command that cannot run goes to the prompt instead`, async ($, on) => {
    const fake = fakeSdlc(on, BUILD)
    fake.canRun = false
    const ui = await mount($, surface, 'AbovePrompt')

    await ui.press({ key: 'build' })
    expect(fake.ran).toEqual([])
    expect(fake.filled).toEqual(['/sdlc:build '])
  })

  test(`${surface}: SDLC in the footer unfolds the band into the details`, async ($, on) => {
    const fake = fakeSdlc(on)
    const footer = await mount($, surface, 'SessionMode')
    const band = await mount($, surface, 'AbovePrompt')
    expect(await footer.find({ text: /focus/ })).toBeDefined()
    expect(await band.find({ key: 'open-intent.md' })).toBeUndefined()

    await footer.press({ key: 'sdlc' })
    expect(await band.find({ key: 'open-intent.md' })).toBeDefined()
    expect(await band.find({ key: 'open-spec.md' })).toBeDefined()
    expect(await band.find({ key: 'open-plan.md' })).toBeUndefined()
    expect(await band.find({ text: /^Approved$/ })).toBeDefined()
    expect(await band.find({ text: /Tester/ })).toBeUndefined()
    // Each section is a table: its rows share one label column, so the states line up.
    const labelWidth = async (key: string) =>
      ((await band.find({ key }))?.children[0] as { props: { width?: number } } | undefined)?.props.width
    expect(await labelWidth('intent')).toBe('intent.md'.length + 3)
    expect(await labelWidth('plan')).toBe(await labelWidth('intent'))
    expect(await labelWidth('verify')).toBe(await labelWidth('gate'))
    // The sections sit side by side where the band is wide enough and wrap where not.
    expect((await band.find({ key: 'artifacts' }))?.props.minWidth).toBeGreaterThan(0)
    // The current actions have a column of their own, beside Artifacts and Status.
    const actions = await band.find({ key: 'actions' })
    expect(JSON.stringify(actions?.children)).toContain('Approve spec')
    expect(JSON.stringify((await band.find({ key: 'spec' }))?.children)).not.toContain('"approve"')
    expect(await band.find({ key: 'approve' })).toBeDefined()

    await band.press({ key: 'open-intent.md', link: { href: 'file:///repo/docs/sdlc/feat-a/intent.md' } })
    expect(fake.shown).toEqual(['/repo/docs/sdlc/feat-a/intent.md'])
    expect(fake.opens).toEqual([])

    await footer.press({ key: 'sdlc' })
    expect(await band.find({ key: 'open-intent.md' })).toBeUndefined()
    expect(await band.find({ key: 'approve' })).toBeDefined()
  })

  test(`${surface}: without SDLC the band stays empty until SDLC offers Init`, async ($, on) => {
    const fake = fakeSdlc(on, { enabled: false })
    on('ui.render', () => ({ type: 'Box', props: {}, children: [] }))
    const footer = await mount($, surface, 'SessionMode')
    const band = await mount($, surface, 'AbovePrompt')
    expect(await band.findAll({ type: 'Button' })).toHaveLength(0)

    await footer.press({ key: 'sdlc' })
    await band.press({ key: 'init' })
    expect(fake.ran).toEqual(['sdlc:init'])
    expect(await band.findAll({ type: 'Button' })).toHaveLength(0)
  })
}
