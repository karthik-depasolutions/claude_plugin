/** What the Context Discovery Agent worked out about the business behind
 *  the data — the "here's what we learned" moment while a build runs.
 *
 *  Confirmed facts, hypotheses, and open questions are shown as three
 *  visually distinct groups on purpose. The whole point of the agent is that
 *  a reader can tell what was established from what is still a guess; a
 *  single undifferentiated list of bullet points would throw that away. */

interface Entity {
  name: string;
  table: string;
  identifier_column: string;
  is_unique_key: boolean;
}

/** The serialized `BusinessContext` as it rides on the PROFILE stage event —
 *  the agent's own model, not the downstream `to_handoff()` payload, so the
 *  field names here are `inferred_hypotheses`/`open_questions`. */
interface BusinessContextArtifact {
  domain?: string | null;
  domain_confidence?: number;
  record_grain?: string | null;
  business_objective?: string | null;
  primary_entities?: Entity[];
  confirmed_facts?: { source: string; observation: string }[];
  inferred_hypotheses?: { claim: string; category: string; confidence: number }[];
  open_questions?: { question: string; category: string; impact: string }[];
  data_quality_issues?: {
    table: string;
    column: string;
    severity: string;
    summary: string;
    business_impact?: string;
  }[];
  overall_confidence?: number;
  ready_for_downstream_pipeline?: boolean;
}

interface Props {
  context: BusinessContextArtifact;
}

const IMPACT_STYLES: Record<string, string> = {
  critical: "border-danger/40 bg-danger/10 text-danger",
  high: "border-attention/40 bg-attention/10 text-attention",
  medium: "border-line bg-ink text-muted",
  low: "border-line bg-ink text-muted",
};

export default function BusinessContextPanel({ context }: Props) {
  const entities = context.primary_entities ?? [];
  const facts = context.confirmed_facts ?? [];
  const hypotheses = context.inferred_hypotheses ?? [];
  const open = context.open_questions ?? [];
  const issues = context.data_quality_issues ?? [];

  const hasAnything =
    context.record_grain || entities.length || facts.length || hypotheses.length || open.length;
  if (!hasAnything) return null;

  return (
    <div className="animate-reveal-up space-y-4 rounded-lg border border-line bg-[#0E121B] p-4">
      <div className="flex items-start justify-between gap-3 border-b border-line pb-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-physical/15 text-physical">
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.75}>
              <path d="M10 3v14M3 10h14" strokeLinecap="round" />
              <circle cx="10" cy="10" r="7.5" />
            </svg>
          </span>
          <div>
            <p className="text-sm font-semibold text-paper">What we learned about your business</p>
            <p className="text-[11px] text-muted">
              Investigated from your data before anything was built
            </p>
          </div>
        </div>
        {context.ready_for_downstream_pipeline === false && open.length > 0 && (
          <span className="shrink-0 rounded-full border border-attention/40 bg-attention/10 px-2.5 py-0.5 text-[10px] font-medium text-attention">
            {open.length} open
          </span>
        )}
      </div>

      {/* Headline reading of the data */}
      {(context.record_grain || context.business_objective) && (
        <div className="space-y-1.5">
          {context.record_grain && (
            <p className="text-xs text-paper">
              <span className="text-muted">One row is </span>
              {context.record_grain}
            </p>
          )}
          {context.business_objective && (
            <p className="text-xs text-paper">
              <span className="text-muted">Your goal: </span>
              {context.business_objective}
            </p>
          )}
        </div>
      )}

      {entities.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {entities.map((entity) => (
            <span
              key={`${entity.table}.${entity.identifier_column}`}
              className="rounded-full border border-line bg-ink px-2.5 py-0.5 font-mono text-[10px] text-muted"
              title={
                entity.is_unique_key
                  ? "This column is unique on every row"
                  : "This column repeats across rows"
              }
            >
              {entity.table} · {entity.identifier_column}
              {entity.is_unique_key ? "" : " ↻"}
            </span>
          ))}
        </div>
      )}

      {facts.length > 0 && (
        <Section title="Confirmed by you" tone="text-physical">
          {facts.map((fact) => (
            <li key={fact.source} className="text-xs text-paper/90">
              {fact.observation}
            </li>
          ))}
        </Section>
      )}

      {hypotheses.length > 0 && (
        <Section title="Our best guess — not confirmed" tone="text-muted">
          {hypotheses.slice(0, 6).map((hypothesis, i) => (
            <li key={i} className="flex items-baseline gap-2 text-xs text-paper/80">
              <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted">
                {Math.round(hypothesis.confidence * 100)}%
              </span>
              <span>{hypothesis.claim}</span>
            </li>
          ))}
        </Section>
      )}

      {open.length > 0 && (
        <Section title="Still unclear" tone="text-attention">
          {open.slice(0, 6).map((question, i) => (
            <li key={i} className="flex items-baseline gap-2 text-xs text-paper/80">
              <span
                className={`shrink-0 rounded-full border px-1.5 py-px text-[9px] uppercase tracking-wide ${
                  IMPACT_STYLES[question.impact] ?? IMPACT_STYLES.medium
                }`}
              >
                {question.impact}
              </span>
              <span>{question.question}</span>
            </li>
          ))}
        </Section>
      )}

      {issues.length > 0 && (
        <Section title="Things worth cleaning up" tone="text-attention">
          {issues.slice(0, 5).map((issue, i) => (
            <li key={i} className="text-xs text-paper/80">
              <span className="font-mono text-[10px] text-muted">
                {issue.table}.{issue.column}
              </span>{" "}
              {issue.summary}
              {issue.business_impact && (
                <span className="block text-[11px] text-muted">{issue.business_impact}</span>
              )}
            </li>
          ))}
        </Section>
      )}
    </div>
  );
}

function Section({
  title,
  tone,
  children,
}: {
  title: string;
  tone: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5 border-t border-line pt-3">
      <p className={`text-[10px] font-medium uppercase tracking-wider ${tone}`}>{title}</p>
      <ul className="space-y-1">{children}</ul>
    </div>
  );
}
