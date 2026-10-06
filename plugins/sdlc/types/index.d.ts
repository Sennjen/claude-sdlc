export type Stage = 'intent' | 'spec' | 'plan'

export type StageState = 'missing' | 'draft' | 'approved' | 'stale' | 'skipped'

export type StageInfo = {
  state: StageState
  path: string
  sha256: string | null
  approved_sha256: string | null
  by: string | null
  at: string | null
  reason: string | null
}

export type FeatureInfo = {
  slug: string
  stage: Stage | 'build'
  stage_state: StageState | null
  done: boolean
}

export type VerifyRun = { ok: boolean; at: string | null; report: string | null }

/** The last sdlc-reviewer verdict for the active feature; `current` while the code it read
 *  is unchanged. */
export type ReviewInfo = { verdict: 'ready' | 'changes'; at: string | null; current: boolean }

/** The PR (or MR) the agent opened for the active feature. */
export type PrInfo = { url: string; at: string | null }

/** What `sdlc.py cli state --session <id>` prints. */
export type SdlcState =
  | { enabled: false }
  | {
      enabled: true
      root: string
      active: string | null
      stage: Stage | 'build' | null
      stage_state: StageState | null
      stage_label: string | null
      next_skill: string | null
      code_gate: 'open' | 'closed'
      artifact: string | null
      stages: Partial<Record<Stage, StageInfo>>
      features: FeatureInfo[]
      tests_locked: boolean
      fasttrack: { by: string; at: string; reason: string } | null
      verify: { commands: string[]; last: VerifyRun | null }
      review: ReviewInfo | null
      pr: PrInfo | null
      done?: boolean
    }

/** An artifact as it was on screen when the person pressed Approve. */
export type Approval = { stage: Stage; path: string; sha256: string }

/** Feature files beside the stage artifacts that `state` does not report, and the build's
 *  progress: plan steps done (marked in progress.md) of the plan's total. */
export type ExtraFiles = { progress: boolean; approvals: boolean; steps: { done: number; total: number } }

declare module 'claude-code' {
  interface PluginState {
    'sdlc': {
      state: SdlcState | null
      error: string | null
      busy: boolean
      files: ExtraFiles
      /** Whether `SDLC` in the footer unfolded the band into the details. */
      details: boolean
      /** Whether the person hid the band with its ×; a new stage or status shows it again. */
      hidden: boolean
      /** Claude asked (request_test_unlock) for the locked tests to be unlocked, and why. */
      unlockRequest: { reason: string } | null
    }
  }
}
