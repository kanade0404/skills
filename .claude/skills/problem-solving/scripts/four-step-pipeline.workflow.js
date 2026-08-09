// Pólya's four-step method as a multi-agent Workflow pipeline.
//
// Run with the Workflow tool (Claude Code multi-agent orchestration):
//   Workflow({ scriptPath: "skills/problem-solving/scripts/four-step-pipeline.workflow.js",
//              args: { problem: "<fully specified problem statement>" } })
//
// Unlike the `problem-solver` subagent (one agent runs all four steps in a
// single pass), this decomposes Step 2 and Step 4 into independent agents so
// the pipeline gets Pólya's own advice for free: Step 2 asks "did you
// consider — and reject — alternatives?" (parallel candidate plans, judged),
// and Step 4 asks "can you check the result a different way?" (parallel
// independent verifications, synthesized). Use this instead of the subagent
// when the problem is high-stakes enough to justify multiple candidate plans
// and multiple independent checks; use the subagent when one pass is enough.
export const meta = {
  name: 'problem-solving-four-step-pipeline',
  description:
    "Pólya's four-step method (Understand → Plan → Execute → Look Back) as a multi-agent pipeline: candidate plans are proposed in parallel and judged before execution, and the result is independently re-verified by multiple agents before being accepted.",
  phases: [
    { title: 'Understand', detail: 'clarify unknown / data / condition' },
    { title: 'Plan', detail: 'parallel candidate plans, then judge/synthesize' },
    { title: 'Execute', detail: 'carry out the chosen plan, verifying each step' },
    { title: 'Look Back', detail: 'independently verify the result multiple ways' },
  ],
}

const UNDERSTAND_SCHEMA = {
  type: 'object',
  properties: {
    unknown: { type: 'string', description: 'What result is actually wanted' },
    data: { type: 'string', description: 'What is given/known' },
    condition: { type: 'string', description: 'Constraints tying the unknown to the data' },
    conditionAssessment: {
      type: 'string',
      enum: ['sufficient', 'insufficient', 'redundant', 'contradictory'],
    },
    restated: { type: 'string', description: 'The problem restated in different words' },
    assumptions: { type: 'array', items: { type: 'string' } },
  },
  required: ['unknown', 'data', 'condition', 'conditionAssessment', 'restated'],
}

const PLAN_CANDIDATE_SCHEMA = {
  type: 'object',
  properties: {
    approach: { type: 'string' },
    rationale: { type: 'string' },
    steps: { type: 'array', items: { type: 'string' } },
  },
  required: ['approach', 'rationale', 'steps'],
}

const PLAN_CHOICE_SCHEMA = {
  type: 'object',
  properties: {
    chosenPlan: {
      type: 'object',
      properties: {
        approach: { type: 'string' },
        steps: { type: 'array', items: { type: 'string' } },
      },
      required: ['approach', 'steps'],
    },
    rejected: {
      type: 'array',
      items: {
        type: 'object',
        properties: { approach: { type: 'string' }, reason: { type: 'string' } },
        required: ['approach', 'reason'],
      },
    },
  },
  required: ['chosenPlan', 'rejected'],
}

const EXECUTE_SCHEMA = {
  type: 'object',
  properties: {
    trace: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          step: { type: 'string' },
          verified: { type: 'boolean' },
          note: { type: 'string' },
        },
        required: ['step', 'verified'],
      },
    },
    result: { type: 'string' },
  },
  required: ['trace', 'result'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    method: { type: 'string' },
    passes: { type: 'boolean' },
    notes: { type: 'string' },
  },
  required: ['method', 'passes'],
}

const LOOKBACK_SCHEMA = {
  type: 'object',
  properties: {
    verified: { type: 'boolean' },
    summary: { type: 'string' },
    generalizesTo: { type: 'string' },
  },
  required: ['verified', 'summary'],
}

// Some Workflow callers deliver `args` as a JSON-encoded string instead of
// the parsed object the tool contract promises ("verbatim") — normalize
// defensively rather than fail on a caller-side serialization quirk.
let resolvedArgs = args
if (typeof resolvedArgs === 'string') {
  try {
    resolvedArgs = JSON.parse(resolvedArgs)
  } catch {
    resolvedArgs = undefined
  }
}

function assertNonEmptyStringArray(value, name) {
  const ok =
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => typeof item === 'string' && item.trim().length > 0)
  if (!ok) {
    throw new Error(
      `Workflow(args: { ${name}: [...] }) must be a non-empty array of non-empty strings when provided.`
    )
  }
}

const problem = resolvedArgs && resolvedArgs.problem
if (typeof problem !== 'string' || problem.trim().length === 0) {
  throw new Error(
    'Workflow(args: { problem: "..." }) is required and must be a non-empty string — pass the fully-specified problem statement (as a JSON object, or a JSON-encoded string if your caller cannot pass objects directly). This pipeline does not ask follow-up questions.'
  )
}

const planAngles = (resolvedArgs && resolvedArgs.planAngles) || [
  'pattern-match: has a same or closely similar problem been solved before? Reuse that result/method directly.',
  'decomposition: split the problem into easier or more specific sub-problems and solve those.',
  'analogy: find a structurally similar problem and adapt its method.',
]
assertNonEmptyStringArray(planAngles, 'planAngles')

const verifyMethods = (resolvedArgs && resolvedArgs.verifyMethods) || [
  're-derive the result via a genuinely different method and compare',
  'stress-test the result against an edge case or boundary condition',
  'check the result actually satisfies the original condition from Step 1',
]
assertNonEmptyStringArray(verifyMethods, 'verifyMethods')

phase('Understand')
log('Step 1 — understanding the problem')
const understanding = await agent(
  `Problem: ${problem}\n\nApply Pólya Step 1 (Understand the Problem). State the unknown (what result is wanted), the data (what's given), the condition (constraints tying data to unknown), and whether the condition is sufficient / insufficient / redundant / contradictory. Restate the problem in your own words. If anything is underspecified, state the assumption you're making explicitly — you cannot ask a follow-up question.`,
  { schema: UNDERSTAND_SCHEMA, phase: 'Understand' }
)

phase('Plan')
log(`Step 2 — generating ${planAngles.length} candidate plans in parallel`)
const candidates = (
  await parallel(
    planAngles.map((angle) => () =>
      agent(
        `Problem: ${problem}\nUnderstanding: ${JSON.stringify(understanding)}\n\nApply Pólya Step 2 (Devise a Plan) from this specific angle: ${angle}\nPropose one concrete plan: approach, rationale, and an ordered list of steps.`,
        { schema: PLAN_CANDIDATE_SCHEMA, phase: 'Plan', label: `plan:${angle.slice(0, 24)}` }
      )
    )
  )
).filter(Boolean)

if (!candidates.length) {
  throw new Error('All plan-candidate agents failed — cannot proceed to Execute without a plan.')
}

log('Step 2 — judging candidates and naming rejected alternatives')
const planChoice = await agent(
  `Problem: ${problem}\nUnderstanding: ${JSON.stringify(understanding)}\nCandidate plans:\n${JSON.stringify(candidates, null, 2)}\n\nChoose the single best plan, or synthesize the best elements of more than one into one coherent plan. List every candidate you did NOT fully adopt, each with a one-line rejection reason — Pólya Step 2 requires naming rejected alternatives, not silently picking a winner.`,
  { schema: PLAN_CHOICE_SCHEMA, phase: 'Plan', label: 'plan:choose' }
)

phase('Execute')
log('Step 3 — executing the chosen plan')
const execution = await agent(
  `Problem: ${problem}\nChosen plan: ${JSON.stringify(planChoice.chosenPlan)}\n\nApply Pólya Step 3 (Carry Out the Plan). Execute the plan step by step. After each step, verify it before moving to the next one — do not execute everything and check only at the end. If a step cannot be verified, say so and stop rather than pushing forward regardless. Produce the final result.`,
  { schema: EXECUTE_SCHEMA, phase: 'Execute' }
)

phase('Look Back')
log(`Step 4 — verifying the result ${verifyMethods.length} independent ways`)
const verifications = (
  await parallel(
    verifyMethods.map((method) => () =>
      agent(
        `Problem: ${problem}\nClaimed result: ${execution.result}\nExecution trace: ${JSON.stringify(execution.trace)}\n\nApply Pólya Step 4 (Look Back) using this specific verification method: ${method}\nDoes the result actually hold up? Be skeptical — default to passes=false if you cannot positively confirm it, don't give the benefit of the doubt.`,
        { schema: VERIFY_SCHEMA, phase: 'Look Back', label: `verify:${method.slice(0, 24)}` }
      )
    )
  )
).filter(Boolean)

const passCount = verifications.filter((v) => v.passes).length
log(`Step 4 — ${passCount}/${verifications.length} independent verifications passed`)

const lookback = await agent(
  `Problem: ${problem}\nResult: ${execution.result}\nIndependent verifications: ${JSON.stringify(verifications)}\n\n${passCount}/${verifications.length} independent verifications passed. Synthesize the Look Back: is the result verified overall? Summarize the strongest confirming and disconfirming evidence, and note what other problems this result or method could generalize to.`,
  { schema: LOOKBACK_SCHEMA, phase: 'Look Back' }
)

// Don't trust the synthesis agent's self-reported `verified` claim — derive it
// from the actual verification results. Otherwise a single overconfident
// agent could mark a result "verified" even when every independent check
// failed, defeating the point of running independent verifications at all.
const verifiedByEvidence = verifications.length > 0 && verifications.every((v) => v.passes === true)
if (lookback.verified !== verifiedByEvidence) {
  log(
    `Step 4 — overriding lookback.verified (agent said ${lookback.verified}, but ${passCount}/${verifications.length} verifications actually passed)`
  )
}
lookback.verified = verifiedByEvidence

return {
  understanding,
  plan: planChoice,
  execution,
  verifications,
  lookback,
}
